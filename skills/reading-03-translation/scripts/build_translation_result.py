#!/usr/bin/env python3
"""End-to-end orchestrator building bilingual translation artifacts and /app/output/result.json."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from apply_translation import apply_translations, write_rendered_outputs
from extract_terms import extract_glossary
from ingest_source import ingest_input
from pdf_utils import write_json_atomic
from segment_and_protect import segment_document
from verify_translation import verify_bilingual_translation


def run_translation_pipeline(
    input_path: str | Path,
    work_dir: str | Path,
    result_path: Optional[str | Path] = None,
    case_path: Optional[str | Path] = None,
    translations_json: Optional[str | Path] = None,
    ocr_mode: str = "auto",
) -> Dict[str, Any]:
    """Run ingestion -> term extraction -> segmentation -> bilingual rendering -> QA verification."""
    work_p = Path(work_dir)
    ingest_dir = work_p / "ingest"
    trans_dir = work_p / "translation"
    trans_dir.mkdir(parents=True, exist_ok=True)

    manifest = ingest_input(input_path, ingest_dir, case_path=case_path, ocr_mode=ocr_mode, save_crops=True)
    if manifest["paper_count"] == 0:
        raise FileNotFoundError(f"No supported PDF/MD/TXT input found under {input_path}")

    # Primary paper (reading-03 translates the target paper)
    first_paper = manifest["papers"][0]
    doc_path = Path(first_paper["document_json"])
    doc_data = json.loads(doc_path.read_text(encoding="utf-8"))

    custom_glossary: Dict[str, str] = {}
    if case_path and Path(case_path).exists():
        c = json.loads(Path(case_path).read_text(encoding="utf-8"))
        if isinstance(c.get("custom_glossary"), dict):
            custom_glossary = {str(k): str(v) for k, v in c["custom_glossary"].items()}

    glossary = extract_glossary(doc_data, custom_glossary=custom_glossary)
    glossary_p = trans_dir / "glossary.json"
    write_json_atomic(glossary_p, glossary)

    segments = segment_document(doc_data)
    segments_p = trans_dir / "segments.json"
    write_json_atomic(segments_p, segments)

    ov = None
    if translations_json and Path(translations_json).exists():
        ov = json.loads(Path(translations_json).read_text(encoding="utf-8"))

    bilingual = apply_translations(segments, glossary, translations_override=ov)
    qa_report = verify_bilingual_translation(bilingual, segments, glossary)
    bilingual["qa_report"] = qa_report

    rendered_paths = write_rendered_outputs(bilingual, trans_dir)
    qa_p = trans_dir / "qa_report.json"
    write_json_atomic(qa_p, qa_report)

    result_obj: Dict[str, Any] = {
        "schema_version": "reading-03-translation-result/v1",
        "challenge_id": "product-arena-01-5a9928c1",
        "status": bilingual["status"],
        "source_lang": "en",
        "target_lang": "zh-CN",
        "summary": (
            f"Translation status={bilingual['status']}: "
            f"{qa_report['metrics']['translated_count']}/{qa_report['metrics']['segment_count']} segments aligned, "
            f"token_fidelity={qa_report['metrics']['protected_token_fidelity']}, "
            f"term_consistency={qa_report['metrics']['terminology_consistency']}."
        ),
        "metrics": qa_report["metrics"],
        "artifacts": {
            "document_json": str(doc_path),
            "glossary_json": str(glossary_p),
            "segments_json": str(segments_p),
            **rendered_paths,
            "qa_report_json": str(qa_p),
        },
        "uncertainties": bilingual.get("uncertainties", []),
        "verification": {
            "passed": qa_report["passed"],
            "check_count": len(qa_report["checks"]),
            "failed_checks": [k for k, v in qa_report["checks"].items() if not v],
        },
    }

    if result_path:
        write_json_atomic(Path(result_path), result_obj)

    return {"result": result_obj, "bilingual": bilingual, "qa_report": qa_report, "glossary": glossary, "segments": segments}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run end-to-end literature translation pipeline.")
    parser.add_argument("--input", required=True, help="Input file or directory (e.g. /input)")
    parser.add_argument("--work-dir", required=True, help="Working directory (e.g. /app/work)")
    parser.add_argument("--result-out", default=None, help="Output result.json path (e.g. /app/output/result.json)")
    parser.add_argument("--case", default=None, help="Optional case.json path")
    parser.add_argument("--translations-json", default=None, help="Optional agent-refined translations JSON")
    parser.add_argument("--ocr", choices=("auto", "always", "off"), default="auto")
    args = parser.parse_args()

    out = run_translation_pipeline(
        args.input,
        args.work_dir,
        result_path=args.result_out,
        case_path=args.case,
        translations_json=args.translations_json,
        ocr_mode=args.ocr,
    )
    print(json.dumps(out["result"], ensure_ascii=False))
    return 0 if out["qa_report"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
