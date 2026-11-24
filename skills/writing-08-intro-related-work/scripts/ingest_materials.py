#!/usr/bin/env python3
"""Ingest related-work literature corpus and author study facts with provenance checks."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from rw_utils import parse_bibtex_records, write_json_atomic


def ingest_materials(
    input_dir: Path,
    case_data: Dict[str, Any],
) -> Dict[str, Any]:
    """Load corpus records (.json/.bib) and study_facts.json, recording missing material gaps."""
    papers: List[Dict[str, Any]] = []
    study_facts: Dict[str, Any] = {}
    material_gaps: List[Dict[str, Any]] = []

    corpus_hint = case_data.get("corpus_file")
    facts_hint = case_data.get("study_facts_file")

    corpus_p: Optional[Path] = None
    if corpus_hint:
        cand = Path(str(corpus_hint))
        corpus_p = cand if cand.exists() else (input_dir / cand.name)
    elif input_dir.is_dir():
        for name in ("papers.json", "corpus.json", "references.bib", "library.bib"):
            if (input_dir / name).exists():
                corpus_p = input_dir / name
                break

    if corpus_p and corpus_p.exists():
        if corpus_p.suffix.lower() == ".json":
            raw = json.loads(corpus_p.read_text(encoding="utf-8", errors="replace"))
            items = raw.get("papers", raw.get("records", raw)) if isinstance(raw, dict) else raw
            if isinstance(items, list):
                for idx, it in enumerate(items, start=1):
                    if isinstance(it, dict):
                        papers.append(
                            {
                                "cite_key": str(it.get("cite_key") or f"paper_{idx}"),
                                "title": str(it.get("title", "")),
                                "authors": list(it.get("authors", [])),
                                "year": it.get("year"),
                                "venue": str(it.get("venue", "")),
                                "doi": it.get("doi"),
                                "route": str(it.get("route", "")),
                                "problem_setting": str(it.get("problem_setting", "")),
                                "evaluation_system": str(it.get("evaluation_system", "")),
                                "key_finding": str(it.get("key_finding", "")),
                                "limitation": str(it.get("limitation", "")),
                            }
                        )
        elif corpus_p.suffix.lower() == ".bib":
            papers = parse_bibtex_records(corpus_p)

    facts_p: Optional[Path] = None
    if facts_hint:
        cand = Path(str(facts_hint))
        facts_p = cand if cand.exists() else (input_dir / cand.name)
    elif input_dir.is_dir() and (input_dir / "study_facts.json").exists():
        facts_p = input_dir / "study_facts.json"

    if facts_p and facts_p.exists():
        study_facts = json.loads(facts_p.read_text(encoding="utf-8", errors="replace"))

    if not papers:
        material_gaps.append({"code": "empty_or_missing_literature_corpus", "message": "No related-work literature records found."})
    if not study_facts:
        material_gaps.append({"code": "missing_study_facts", "message": "No author study_facts/evidence file provided."})

    return {
        "schema_version": "writing-08-materials/v1",
        "corpus_file": str(corpus_p) if corpus_p else None,
        "study_facts_file": str(facts_p) if facts_p else None,
        "paper_count": len(papers),
        "papers": papers,
        "study_facts": study_facts,
        "material_gaps": material_gaps,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest related-work corpus and author study facts.")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--case", default=None)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    in_p = Path(args.input_dir)
    case_data = json.loads(Path(args.case).read_text(encoding="utf-8")) if (args.case and Path(args.case).exists()) else {}
    res = ingest_materials(in_p, case_data)
    write_json_atomic(Path(args.out), res)
    print(json.dumps({"paper_count": res["paper_count"], "material_gaps": len(res["material_gaps"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
