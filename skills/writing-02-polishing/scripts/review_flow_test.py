"""Exercise the actual editor interface with synthetic inputs, including a Word table."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from build_polish_result import run_polishing_pipeline
from text_utils import write_json_atomic

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def run(root):
    case = json.loads((EXAMPLES / "example_case.json").read_text(encoding="utf-8"))
    edits = json.loads((EXAMPLES / "example_edits.json").read_text(encoding="utf-8"))
    src = (EXAMPLES / "draft.md").read_text(encoding="utf-8")
    expected = src.replace("measurements was", "measurements were")

    def build(name, config, payload):
        base = root / name
        inp = base / "input"
        write_json_atomic(inp / "case.json", config)
        path = base / "reviews.json"
        write_json_atomic(path, payload)
        return run_polishing_pipeline(inp, base / "work", base / "output", edits_json=path)["result"]

    inline = {k: v for k, v in case.items() if k != "manuscript_file"}
    inline["draft_text"] = src
    checks = {}
    result = build("inline", inline, edits)
    checks["full_edit_and_translations"] = (result["status"] == "completed" and result["polished_text"] == expected
                                           and len(result["bilingual_alignment"]) == 2 and len(result["change_records"]) == 1)
    checks["inline_provenance"] = result["change_records"][0]["source_location"]["source"] == "case.json#/draft_text"
    sparse = copy.deepcopy(edits)
    sparse["sentences"].pop()
    partial = build("missing", inline, sparse)
    checks["unreviewed_preserved"] = partial["status"] == "partial" and "may affect precision [2]" in partial["polished_text"]
    unsafe = copy.deepcopy(edits)
    unsafe["sentences"][0]["polished_text"] = unsafe["sentences"][0]["polished_text"].replace("8 Hz", "80 Hz")
    partial = build("unsafe", inline, unsafe)
    checks["rejected_fact_change_keeps_original"] = partial["status"] == "partial" and partial["polished_text"] == src
    bad_translation = copy.deepcopy(edits)
    bad_translation["sentences"][0]["translation"] = bad_translation["sentences"][0]["translation"].replace("8 Hz", "18 Hz")
    partial = build("bad_translation", inline, bad_translation)
    checks["translation_numeric_drift_detected"] = partial["status"] == "partial" and any(
        u["code"] == "translation_numeric_values" for u in partial["uncertainties"])
    closed = dict(inline, output_contract={"type": "object", "additionalProperties": False, "properties": {
        "case_id": {"type": "string"}, "polished_manuscript": {"type": "string"},
        "author_query": {"type": "string"}, "review_decision": {"type": "object",
          "required": ["language"], "properties": {"language": {"type": "string"}}}}})
    custom = copy.deepcopy(edits)
    custom["contract_values"]["review_decision"] = {"language": "English"}
    result = build("closed", closed, custom)
    saved = json.loads((root / "closed/output/result.json").read_text(encoding="utf-8"))
    checks["exact_nested_contract"] = result["status"] == "completed" and set(saved) == set(closed["output_contract"]["properties"])
    custom["contract_values"]["review_decision"] = {"language": 4}
    checks["wrong_nested_type_detected"] = build("bad_contract", closed, custom)["status"] == "partial"

    # The demo intentionally uses mixed body/table locations and a separate reference.
    from docx import Document
    office_dir = root / "word/input/materials"
    office_dir.mkdir(parents=True, exist_ok=True)
    document = Document()
    document.add_paragraph("Calibration")
    cell = document.add_table(rows=1, cols=1).cell(0, 0)
    cell.paragraphs[0].add_run("The paired measurements ").bold = True
    cell.paragraphs[0].add_run("was repeatable at 8 Hz.")
    document.save(office_dir / "draft.docx")
    reference = office_dir / "journal_style.md"
    reference.write_text("Keep the authors' intended claim.", encoding="utf-8")
    decisions = {"sentences": [
        {"sent_id": "sent_0001", "source_text": "Calibration", "polished_text": "Calibration",
         "edit_type": "unchanged", "rationale": "The heading identifies the calibration procedure."},
        {**edits["sentences"][0]}]}
    result = build("word", {"case_id": "word-layout", "manuscript_file": "materials/draft.docx"}, decisions)
    artifacts = result["artifacts"]
    output_doc = Document(artifacts["polished_docx"])
    got = output_doc.tables[0].cell(0, 0).paragraphs[0]
    checks["docx_table_and_run"] = (got.text == decisions["sentences"][1]["polished_text"] and got.runs[0].bold
                                    and "table 1, row 1, cell 1" in result["change_records"][0]["source_location"]["location"])
    checks["docx_rendered"] = ("preview_pdf" in artifacts and Path(artifacts["preview_pdf"]).read_bytes().startswith(b"%PDF-")
                               and result["status"] == "completed")
    return checks


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    results = run(args.out_dir)
    write_json_atomic(args.out_dir / "checks.json", results)
    print(json.dumps(results, indent=2))
    raise SystemExit(0 if all(results.values()) else 1)
