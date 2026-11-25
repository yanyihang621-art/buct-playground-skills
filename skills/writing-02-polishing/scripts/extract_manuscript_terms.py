#!/usr/bin/env python3
"""Extract domain terms, acronyms, and detect custom glossary conflicts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from text_utils import write_json_atomic

PAREN_ACR_RE = re.compile(r"\(([A-Z][A-Z0-9\-]{1,10})\)")
CHEM_ACR_RE = re.compile(r"\b([A-Z]{2,8}(?:-[A-Z0-9][A-Za-z0-9]+)*)\b")


def _abbreviation_definitions(text: str):
    """Only infer a definition when its initials explain the abbreviation."""
    for line in text.splitlines():
        for match in PAREN_ACR_RE.finditer(line):
            acr = match[1]
            words = re.search(r"((?:[A-Za-z][A-Za-z-]*[ \t]+){0,10}[A-Za-z][A-Za-z-]*)\s*$", line[:match.start()])
            if not words:
                continue
            tokens = words[1].split()
            for start in range(len(tokens)-1, -1, -1):
                candidate = " ".join(tokens[start:])
                parts = re.split(r"[\s-]+", candidate)
                significant = [p for p in parts if p.lower() not in {"of", "and", "for", "the", "in", "to"}]
                if any("".join(p[0].upper() for p in group) == acr.replace("-", "") for group in (parts, significant)):
                    yield candidate, acr
                    break


def extract_manuscript_glossary(
    manuscript_text: str,
    custom_glossary: Optional[Dict[str, str]] = None,
    glossary_conflicts_check: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """Build locked glossary + detect conflicts in user-provided terminology rules."""
    entries: List[Dict[str, Any]] = []
    seen_canon: set[str] = set()
    conflicts: List[Dict[str, Any]] = []

    # Detect conflicts inside explicit glossary pairs or conflicting capitalization/forms
    seen_lower_map: Dict[str, str] = {}
    for k, v in (custom_glossary or {}).items():
        ks, vs = k.strip(), v.strip()
        if not ks:
            continue
        low = ks.lower()
        if low in seen_lower_map and seen_lower_map[low] != vs:
            conflicts.append(
                {
                    "code": "glossary_conflict",
                    "term": ks,
                    "definitions": [seen_lower_map[low], vs],
                    "message": f"Conflicting custom glossary definitions for `{ks}`.",
                }
            )
            continue
        seen_lower_map[low] = vs
        if ks not in seen_canon:
            seen_canon.add(ks)
            entries.append(
                {
                    "term_id": f"t_{len(entries) + 1:03d}",
                    "canonical": vs or ks,
                    "variants": [ks] if vs != ks else [],
                    "source": "custom_glossary",
                    "bilingual": bool(re.search(r"[\u3400-\u9fff]", ks)) != bool(re.search(r"[\u3400-\u9fff]", vs)),
                    "locked": True,
                }
            )

    for item in (glossary_conflicts_check or []):
        t = str(item.get("term", "")).strip()
        alt = str(item.get("preferred", "")).strip()
        if t and t.lower() in seen_lower_map and seen_lower_map[t.lower()] != alt:
            conflicts.append({"code": "glossary_conflict", "term": t, "definitions": [seen_lower_map[t.lower()], alt]})

    # Discover recurring parenthetical acronyms (only first expansion kept locked)
    acr_expansions: Dict[str, set[str]] = {}
    for phrase, acr in _abbreviation_definitions(manuscript_text):
        acr_expansions.setdefault(acr, set()).add(phrase.strip().lower())
        if acr not in seen_canon:
            seen_canon.add(acr)
            entries.append(
                {
                    "term_id": f"t_{len(entries) + 1:03d}",
                    "canonical": acr,
                    "full_form": phrase.strip(),
                    "variants": [],
                    "source": "discovered_acronym",
                    "locked": True,
                }
            )
    for acr, forms in acr_expansions.items():
        if len(forms) > 1:
            conflicts.append({"code": "acronym_expansion_conflict", "term": acr, "expansions": sorted(forms)})

    # Discover recurring uppercase acronyms
    counts: Dict[str, int] = {}
    for tok in CHEM_ACR_RE.findall(manuscript_text):
        if tok in ("PDF", "URL", "DOI", "THE", "AND", "FOR"):
            continue
        counts[tok] = counts.get(tok, 0) + 1
    for tok, cnt in sorted(counts.items()):
        if cnt >= 2 and tok not in seen_canon:
            seen_canon.add(tok)
            entries.append(
                {
                    "term_id": f"t_{len(entries) + 1:03d}",
                    "canonical": tok,
                    "variants": [],
                    "source": "recurring_symbol",
                    "locked": True,
                }
            )

    lines = manuscript_text.splitlines()
    for entry in entries:
        forms = [entry["canonical"], *entry["variants"]]
        if entry.get("full_form"):
            forms.append(entry["full_form"])
        entry["source_lines"] = [idx for idx, line in enumerate(lines, 1) if any(form in line for form in forms)]

    return {
        "schema_version": "writing-02-glossary/v1",
        "entry_count": len(entries),
        "entries": entries,
        "conflicts": conflicts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract locked terminology glossary from manuscript.")
    parser.add_argument("--manuscript", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--case", default=None)
    args = parser.parse_args()

    txt = Path(args.manuscript).read_text(encoding="utf-8", errors="replace")
    custom: Dict[str, str] = {}
    extra_conflicts: List[Dict[str, str]] = []
    if args.case and Path(args.case).exists():
        c = json.loads(Path(args.case).read_text(encoding="utf-8"))
        if isinstance(c.get("custom_glossary"), dict):
            custom = {str(k): str(v) for k, v in c["custom_glossary"].items()}
        if isinstance(c.get("glossary_overrides"), list):
            extra_conflicts = c["glossary_overrides"]

    res = extract_manuscript_glossary(txt, custom_glossary=custom, glossary_conflicts_check=extra_conflicts)
    write_json_atomic(Path(args.out), res)
    print(json.dumps({"entry_count": res["entry_count"], "conflicts": len(res["conflicts"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
