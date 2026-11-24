#!/usr/bin/env python3
"""Extract domain terminology, acronyms, and locked glossary from document.json."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from pdf_utils import write_json_atomic

PAREN_ACRONYM_RE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9\-]{2,}(?:\s+[A-Za-z][A-Za-z0-9\-]{1,}){1,5})\s*\(([A-Z][A-Z0-9\-]{1,10})\)"
)
CHEM_OR_UPPER_RE = re.compile(r"\b([A-Z]{2,8}|[A-Z][a-z]?(?:[A-Z0-9][a-z0-9\-]*)+)\b")
STOP_UPPER = {"PDF", "DOI", "URL", "HTTP", "HTTPS", "USA", "AND", "THE", "FOR", "WITH", "FROM", "TABLE", "FIGURE", "SECTION"}


def load_seed_glossary(seed_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load schemas/terminology_seed.yaml safely without hard-failing if PyYAML is absent."""
    path = seed_path or (SKILL_DIR / "schemas" / "terminology_seed.yaml")
    if not path.exists():
        return {"terms": [], "negation_pairs": [], "section_headings_zh": {}}
    raw = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    # Fallback lightweight line parser if PyYAML unavailable
    terms: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    in_headings = False
    headings: Dict[str, str] = {}
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("section_headings_zh:"):
            in_headings = True
            cur = None
            continue
        if s.startswith("negation_pairs:"):
            in_headings = False
            cur = None
            continue
        if in_headings and ":" in s:
            k, v = s.split(":", 1)
            headings[k.strip().strip('"')] = v.strip().strip('"')
            continue
        if s.startswith("- term_en:"):
            cur = {"term_en": s.split(":", 1)[1].strip().strip('"'), "aliases": [], "keep_acronym": False, "domain": "general"}
            terms.append(cur)
        elif cur is not None and ":" in s:
            k, v = s.split(":", 1)
            k = k.strip()
            v = v.strip().strip('"')
            if k == "term_zh":
                cur["term_zh"] = v
            elif k == "domain":
                cur["domain"] = v
            elif k == "keep_acronym":
                cur["keep_acronym"] = v.lower() == "true"
            elif k == "aliases":
                inner = v.strip("[]")
                cur["aliases"] = [x.strip().strip('"').strip("'") for x in inner.split(",") if x.strip()]
    return {"terms": terms, "negation_pairs": [], "section_headings_zh": headings}


def _term_pattern(term: str) -> re.Pattern[str]:
    esc = re.escape(term.strip())
    return re.compile(rf"(?<![A-Za-z0-9\-]){esc}(?![A-Za-z0-9\-])", re.IGNORECASE)


def _strip_paren_acronym(zh: str) -> str:
    return re.sub(r"\s*\([A-Za-z0-9\-]+\)\s*$", "", zh).strip()


def extract_glossary(
    doc_data: Dict[str, Any],
    custom_glossary: Optional[Dict[str, str]] = None,
    seed_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Build a locked bilingual glossary anchored to source blocks."""
    seed = load_seed_glossary(seed_path)
    blocks = doc_data.get("blocks", [])
    searchable_blocks = [
        b for b in blocks if b.get("role") not in ("authors", "affiliation", "reference")
    ]

    entries: List[Dict[str, Any]] = []
    seen_lower: set[str] = set()
    term_idx = 0

    def _count_and_first(variants: List[str]) -> Tuple[int, Optional[Dict[str, Any]]]:
        total = 0
        first: Optional[Dict[str, Any]] = None
        pats = [_term_pattern(v) for v in variants if v.strip()]
        for b in searchable_blocks:
            txt = b.get("structured_text") or b.get("text") or ""
            for p in pats:
                hits = p.findall(txt)
                if hits:
                    total += len(hits)
                    if first is None:
                        first = {"page": int(b.get("page", 1)), "block_id": str(b.get("block_id", "p1_b01"))}
        return total, first

    # 1. User-supplied custom glossary takes highest precedence and is always locked.
    for src_term, tgt_zh in (custom_glossary or {}).items():
        s_clean = src_term.strip()
        if not s_clean:
            continue
        cnt, first = _count_and_first([s_clean])
        term_idx += 1
        seen_lower.add(s_clean.lower())
        entries.append(
            {
                "term_id": f"term_{term_idx:03d}",
                "source_term": s_clean,
                "aliases": [],
                "target_zh": tgt_zh.strip(),
                "core_zh": _strip_paren_acronym(tgt_zh.strip()) or tgt_zh.strip(),
                "domain": "custom",
                "status": "keep_verbatim" if tgt_zh.strip() == s_clean else "locked",
                "keep_acronym": False,
                "forbidden_drifts": [],
                "first_seen": first or {"page": 1, "block_id": "case_override"},
                "source_occurrences": cnt,
            }
        )

    # 2. Match seed scientific terminology present in source document.
    for item in seed.get("terms", []):
        en = str(item.get("term_en", "")).strip()
        zh = str(item.get("term_zh", "")).strip()
        if not en or not zh or en.lower() in seen_lower:
            continue
        aliases = [str(a).strip() for a in item.get("aliases", []) if str(a).strip()]
        cnt, first = _count_and_first([en] + aliases)
        if cnt == 0:
            continue
        term_idx += 1
        seen_lower.add(en.lower())
        for a in aliases:
            seen_lower.add(a.lower())
        entries.append(
            {
                "term_id": f"term_{term_idx:03d}",
                "source_term": en,
                "aliases": aliases,
                "target_zh": zh,
                "core_zh": _strip_paren_acronym(zh),
                "domain": str(item.get("domain", "general_science")),
                "status": "locked",
                "keep_acronym": bool(item.get("keep_acronym", False)),
                "forbidden_drifts": [],
                "first_seen": first,
                "source_occurrences": cnt,
            }
        )

    # 3. Discover parenthetical definitions & recurring uppercase acronyms/chemical symbols to keep verbatim.
    for b in searchable_blocks:
        txt = b.get("structured_text") or b.get("text") or ""
        for full_phrase, acr in PAREN_ACRONYM_RE.findall(txt):
            if acr.lower() in seen_lower or acr.upper() in STOP_UPPER:
                continue
            cnt, first = _count_and_first([acr, full_phrase])
            term_idx += 1
            seen_lower.add(acr.lower())
            entries.append(
                {
                    "term_id": f"term_{term_idx:03d}",
                    "source_term": acr,
                    "aliases": [full_phrase.strip()],
                    "target_zh": acr,
                    "core_zh": acr,
                    "domain": "discovered_acronym",
                    "status": "keep_verbatim",
                    "keep_acronym": True,
                    "forbidden_drifts": [],
                    "first_seen": first or {"page": int(b.get("page", 1)), "block_id": str(b.get("block_id", "p1_b01"))},
                    "source_occurrences": max(1, cnt),
                }
            )

    return {
        "schema_version": "reading-03-glossary/v1",
        "source_lang": "en",
        "target_lang": "zh-CN",
        "entry_count": len(entries),
        "entries": entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract locked bilingual terminology glossary from document.json.")
    parser.add_argument("--doc", required=True, help="Path to parsed document.json")
    parser.add_argument("--out", required=True, help="Output glossary.json path")
    parser.add_argument("--case", default=None, help="Optional case.json with custom_glossary")
    parser.add_argument("--seed", default=None, help="Optional terminology_seed.yaml override")
    args = parser.parse_args()

    doc_data = json.loads(Path(args.doc).read_text(encoding="utf-8"))
    custom: Dict[str, str] = {}
    if args.case and Path(args.case).exists():
        c = json.loads(Path(args.case).read_text(encoding="utf-8"))
        if isinstance(c.get("custom_glossary"), dict):
            custom = {str(k): str(v) for k, v in c["custom_glossary"].items()}

    glossary = extract_glossary(doc_data, custom_glossary=custom, seed_path=Path(args.seed) if args.seed else None)
    write_json_atomic(Path(args.out), glossary)
    print(json.dumps({"entry_count": glossary["entry_count"], "out": str(args.out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
