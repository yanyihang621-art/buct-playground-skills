"""Compare two implementations on identical synthetic inputs, using outputs on disk."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


REED = "Cooling time in thin films"
VALE = "Thermal drift in glass"
LIB = """@article{reed,
  author = {Ava Reed},
  title = {Cooling time in thin films},
  journal = {Fixture Journal},
  year = {2020}
}
@article{vale,
  author = {Ivo Vale},
  title = {Thermal drift in glass},
  journal = {Fixture Journal},
  year = {2021}
}
"""
OLD_BIB = f"References\n[7] I. Vale. {VALE}. Fixture Journal, 2021.\n[14] A. Reed. {REED}. Fixture Journal, 2020.\n"
UNRESOLVED = "Cooling time remains uncertain [991].\n\n" + OLD_BIB


def _json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def make_cases(root):
    from docx import Document
    import fitz

    cases = {}
    for name in ("mixed", "author_year", "unresolved", "nested_docx", "nested_pdf"):
        folder = root / name
        folder.mkdir(parents=True)
        case = {"case_id": "synthetic_" + name, "style": "gbt7714"}
        nested = name.startswith("nested")
        lib = folder / ("sources/library/records.bib" if nested else "records.bib")
        lib.parent.mkdir(exist_ok=True, parents=True)
        lib.write_text(LIB, encoding="utf-8")
        if not nested:
            case.update({"manuscript_file": "draft.md", "bib_files": ["records.bib"]})
        if name == "mixed":
            manuscript = ("Glass thermal drift [7].\nThin-film cooling [@reed].\n"
                          "Joint measurement [14,7].\nThe literal `arr[7]` is code.\n\n"
                          + OLD_BIB + "\n## Appendix A\nFixture value = 3.75.\n")
        elif name == "author_year":
            manuscript = "Glass thermal drift (Vale, 2021).\nThin-film cooling [@reed].\n"
            case["style"] = "apa7"
        elif name == "unresolved":
            manuscript = UNRESOLVED
            case["output_contract"] = {
                "type": "object", "required": ["case_id", "instrument_calibration_record"],
                "properties": {"instrument_calibration_record": {"type": "object",
                               "description": "Calibration from a certificate. No certificate is supplied."}}
            }
        elif name == "nested_docx":
            location = folder / "manuscripts/v2/draft.docx"
            location.parent.mkdir(parents=True)
            doc = Document()
            doc.add_heading("Thermal observations", level=1)
            p = doc.add_paragraph()
            p.add_run("Glass thermal drift").bold = True
            p.add_run(" [@").font.superscript = True
            p.add_run("vale]").font.superscript = True
            doc.add_table(rows=1, cols=1).cell(0, 0).text = "Film cooling [@reed]."
            doc.add_heading("References", level=1)
            for old in OLD_BIB.splitlines()[1:]:
                doc.add_paragraph(old)
            doc.add_heading("Appendix A", level=1)
            doc.add_paragraph("Fixture value = 3.75.").runs[0].italic = True
            doc.save(location)
        else:
            location = folder / "manuscripts/v2/draft.pdf"
            location.parent.mkdir(parents=True)
            with fitz.open() as pdf:
                for sentence in ("Glass thermal drift [@vale].", "Film cooling [@reed]."):
                    pdf.new_page().insert_text((72, 72), sentence)
                pdf.save(location)
        if not nested:
            (folder / "draft.md").write_text(manuscript, encoding="utf-8")
        _json(folder / "case.json", case)
        cases[name] = folder
    return cases


def _artifact(result, output, key, pattern):
    val = result.get("artifacts", {}).get(key)
    if isinstance(val, str):
        path = Path(val)
        for candidate in (path, output / path):
            if candidate.is_file():
                return candidate
    return next(iter(sorted(output.glob(pattern))), None)


def checks_for(name, output, result):
    from docx import Document
    import fitz

    ms = _artifact(result, output, "synced_manuscript", "synced_manuscript.md")
    body = ms.read_text(encoding="utf-8") if ms and ms.suffix in {".md", ".txt"} else ""
    bib = _artifact(result, output, "bibliography_formatted", "bibliography*.txt")
    refs = bib.read_text(encoding="utf-8") if bib else ""
    both = REED in refs and VALE in refs
    checks = {"case_identity": result.get("case_id") == "synthetic_" + name}
    if name == "mixed":
        checks.update({
            "original_7_becomes_1": "Glass thermal drift [1]." in body,
            "key_reed_becomes_2": "Thin-film cooling [2]." in body,
            "multi_cite_order": bool(re.search(r"Joint measurement \[1,\s*2\]", body)),
            "code_unchanged": "`arr[7]`" in body,
            "appendix_retained": "## Appendix A\nFixture value = 3.75." in body,
            "bibliography_first_use": both and refs.index(VALE) < refs.index(REED),
        })
    elif name == "author_year":
        checks.update({"old_author_year_retained": "Glass thermal drift (Vale, 2021)." in body,
                       "converted_key": "Thin-film cooling (Reed, 2020)." in body,
                       "both_cited_entries": both,
                       "alphabetical_list": both and refs.index(REED) < refs.index(VALE)})
    elif name == "unresolved":
        unresolved_field = "instrument_calibration_record"
        checks.update({
            "source_body_and_old_list_intact": body.strip() == UNRESOLVED.strip(),
            "uncertainty_names_missing_marker": "991" in json.dumps(result.get("uncertainties", [])),
            "partial_or_refused": result.get("status") in ("partial", "refused"),
            "contract_key_present_unfilled": unresolved_field in result and result[unresolved_field] is None,
            "contract_gap_explained": unresolved_field in json.dumps(result.get("uncertainties", [])),
        })
    elif name == "nested_docx":
        docx = _artifact(result, output, "editable_docx", "synced_manuscript.docx")
        doc = Document(docx) if docx else None
        paragraph = next((p for p in doc.paragraphs if "Glass thermal drift" in p.text), None) if doc else None
        pdf = _artifact(result, output, "preview_pdf", "synced_manuscript.pdf")
        pdf_text = ""
        if pdf:
            with fitz.open(pdf) as doc_pdf:
                pdf_text = "".join(page.get_text() for page in doc_pdf)
        checks.update({
            "word_body_edited": bool(paragraph and "Glass thermal drift [1]" in paragraph.text),
            "word_bold_retained": bool(paragraph and any(r.bold and "thermal drift" in r.text for r in paragraph.runs)),
            "word_superscript_retained": bool(paragraph and "[1]" in "".join(r.text for r in paragraph.runs if r.font.superscript)),
            "word_table_edited": bool(doc and doc.tables and doc.tables[0].cell(0, 0).text == "Film cooling [2]."),
            "word_bibliography_attached": bool(doc and all(any(s in p.text for p in doc.paragraphs) for s in (VALE, REED))),
            "word_single_reference_section": bool(doc and sum(p.text == "References" for p in doc.paragraphs) == 1),
            "word_appendix_and_style_retained": bool(doc and any(r.italic and "3.75" in r.text
                                                                 for p in doc.paragraphs for r in p.runs)),
            "rendered_preview_contains_both_claims": all(s in pdf_text for s in ("Glass thermal drift", "Film cooling")),
        })
    else:
        checks.update({"first_page_edited": "Glass thermal drift [1]." in body,
                       "second_page_edited": "Film cooling [2]." in body,
                       "bibliography_first_use": both and refs.index(VALE) < refs.index(REED)})
    return checks


def digest(folder):
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file()}


def evaluate(skill, cases, output_root):
    script = skill.resolve() / "scripts" / "build_reference_result.py"
    if not script.is_file():
        raise FileNotFoundError(script)
    results = {}
    for name, inputs in cases.items():
        work = output_root / name / "work"
        output = output_root / name / "output"
        output.mkdir(parents=True)
        before = digest(inputs)
        run = subprocess.run(
            [sys.executable, "-B", str(script), "--input-dir", str(inputs), "--case", str(inputs / "case.json"),
             "--work-dir", str(work), "--output-dir", str(output)],
            cwd=output.parent, env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True, timeout=150,
        )
        if before != digest(inputs):
            raise ValueError("Implementation modified the common inputs for " + name)
        path = output / "result.json"
        result = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        checks = checks_for(name, output, result)
        results[name] = {"checks": checks, "passed": sum(checks.values()), "total": len(checks),
                         "process_exit": run.returncode, "status": result.get("status"),
                         "input_sha256": before}
        if run.returncode and not path.exists():
            results[name]["runner_error"] = run.stderr.decode("utf-8", errors="replace")[-1500:]
    return {"passed": sum(r["passed"] for r in results.values()), "total": sum(r["total"] for r in results.values()),
            "cases": results}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out-dir", required=True, type=Path)
    p.add_argument("--before-skill", type=Path, help="Optional independent checkout of the earlier Skill directory.")
    p.add_argument("--skill", type=Path, default=Path(__file__).resolve().parents[1])
    args = p.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(dir=args.out_dir, prefix="comparison-")).resolve()
    cases = make_cases(run_dir / "inputs")
    report = {"scope": "Synthetic local regression checks on reference pipelines. Official base-agent gains and scores require Harbor."}
    for name, skill in (("before", args.before_skill), ("after", args.skill)):
        if skill:
            report[name] = evaluate(skill, cases, run_dir / name)
    report_path = run_dir / "comparison.json"
    _json(report_path, report)
    print(report_path)
    for name in ("before", "after"):
        if name in report:
            print(f"{name}: {report[name]['passed']}/{report[name]['total']} output checks")
    return int(report["after"]["passed"] != report["after"]["total"])


if __name__ == "__main__":
    raise SystemExit(main())
