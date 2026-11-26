"""Independent regression cases for source selection, provenance and portable output contracts."""
import argparse
import json
import tempfile
from pathlib import Path
from zipfile import ZipFile

from build_translation_result import run_translation_pipeline
from pdf_utils import write_json_atomic


def run(root):
    inputs = root / "inputs"
    nested = inputs / "materials"
    nested.mkdir(parents=True, exist_ok=True)
    work = root / "work"
    output = root / "output" / "result.json"
    source = nested / "paper.docx"
    with ZipFile(source, "w") as archive:
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Rainfall reshapes coastal sediment.</w:t></w:r></w:p></w:body></w:document>')
    (inputs / "a_reference.md").write_text("This unrelated reference must stay outside the translation.", encoding="utf-8")
    (nested / "domain.tsv").write_text("source_term\ttarget_zh\nRainfall\t降雨\n", encoding="utf-8")
    case = {"case_id": "synthetic-coast", "target_file": "materials/paper.docx", "input_files": [
            "a_reference.md", {"path": "materials/domain.tsv", "role": "glossary"}],
            "output_contract": {"type": "object", "required": ["bilingual_segments", "review_note"],
                                "properties": {"bilingual_segments": {"type": "array"},
                                               "review_note": {"type": "string"}}}}
    write_json_atomic(inputs / "case.json", case)
    plan = run_translation_pipeline(inputs, work, prepare_only=True, ocr_mode="off")
    assert plan["segment_count"] == 1
    original = json.loads(Path(plan["segments_path"]).read_text(encoding="utf-8"))["segments"][0]
    assert original["source_locator"] == {"source": "materials/paper.docx", "docx_paragraph": 1}
    ov = {"segments": [{"segment_id": original["segment_id"], "target_text": "降雨重塑沿岸沉积物。"}],
          "contract_values": {"review_note": "逐段核对了降雨与沿岸沉积物之间的关系。"}}
    write_json_atomic(root / "translation.json", ov)
    result = run_translation_pipeline(inputs, work, output, translations_json=root / "translation.json")["result"]
    assert result["status"] == "completed", result
    assert result["case_id"] == case["case_id"] and result["review_note"] == ov["contract_values"]["review_note"]
    assert result["bilingual_segments"][0]["source_locator"] == original["source_locator"]
    assert any(e["source_term"] == "Rainfall" and e["target_zh"] == "降雨" for e in result["glossary"]["entries"])
    assert all((output.parent / path).is_file() and not Path(path).is_absolute() for path in result["artifacts"].values())
    bilingual = (output.parent / result["artifacts"]["bilingual_md"]).read_text(encoding="utf-8")
    assert "降雨重塑" in bilingual and "unrelated reference" not in bilingual

    del ov["contract_values"]
    write_json_atomic(root / "translation.json", ov)
    incomplete = run_translation_pipeline(inputs, work, output, translations_json=root / "translation.json")["result"]
    assert incomplete["status"] == "partial" and incomplete["review_note"] is None
    assert "output_contract" in incomplete["verification"]["failed_checks"]

    write_json_atomic(inputs / "case.json", {"case_id": "two-papers", "input_files": [
        {"path": "a_reference.md", "role": "reference"}, "materials/paper.docx", "materials/second.txt"]})
    (nested / "second.txt").write_text("Waves transport sand inland.", encoding="utf-8")
    assembled = run_translation_pipeline(inputs, work, output)
    rows = assembled["segments"]["segments"]
    assert len(rows) == 2 and len({r["block_id"] for r in rows}) == 2
    assert {r["source_locator"]["source"] for r in rows} == {"materials/paper.docx", "materials/second.txt"}

    write_json_atomic(inputs / "case.json", {"case_id": "inline", "paragraphs": [{"paragraph_id": "intro-1",
                     "text": "Background passage."}, {"paragraph_id": "intro-2",
                      "text": "The manuscript contains a single passage."}], "translation_scope": {"paragraph_ids": ["intro-2"]}})
    assert run_translation_pipeline(inputs, work, prepare_only=True)["segment_count"] == 1
    inline = run_translation_pipeline(inputs, work, output)["segments"]["segments"][0]
    assert inline["source_locator"]["json_pointer"] == "#/paragraphs/1/text"
    assert inline["source_locator"]["paragraph_id"] == "intro-2"

    write_json_atomic(inputs / "case.json", {"case_id": "missing", "target_file": "missing.pdf"})
    missing = run_translation_pipeline(inputs, work, output)["result"]
    assert missing["status"] == "refused" and missing["case_id"] == "missing"
    assert missing["segments"] == [] and any(x.get("code") == "source_missing_or_unsupported" for x in missing["uncertainties"])
    return {"passed": True, "cases": ["nested DOCX and distractor", "source-specific external glossary", "portable final package", "typed contract",
            "missing contract value", "multi-source segment IDs", "inline source pointer", "exact passage scope", "missing explicit target"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temporary:
        folder = args.out_dir or Path(temporary)
        summary = run(folder)
        if args.out_dir:
            write_json_atomic(folder / "io_regression.json", summary)
        print(json.dumps(summary))

