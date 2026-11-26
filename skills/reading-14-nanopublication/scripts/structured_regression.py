"""Run the normal extraction/export pipeline on synthetic structured sources."""

import copy
import json
import subprocess
import sys
from pathlib import Path

from decompose_assertions import decompose_document, load_relation_vocabulary
from ingest_source import ingest_input
from pdf_utils import write_json_atomic, write_text_lf
from validate_nanopubs import validate_nanopubs


def run_structured_regression(out):
    source = out / "n5_in"
    work = out / "n5_work"
    source.mkdir(parents=True, exist_ok=True)
    xml = """<article><front><article-meta><article-id pub-id-type="doi">10.5555/example.optics</article-id>
<title-group><article-title>Filter stability (synthetic example)</article-title></title-group>
<contrib-group><contrib contrib-type="author"><name><surname>Li</surname><given-names>A.</given-names></name></contrib></contrib-group>
<pub-date><year>2024</year></pub-date><article-version>v2</article-version></article-meta></front>
<body><sec><title>Results</title><p id="r7">Under low light, signal drift may be induced by Filter A.</p>
<table-wrap><table><thead><tr><th>Filter</th><th>Drift (nm)</th></tr></thead><tbody>
<tr><td>A</td><td>3.2</td></tr></tbody></table></table-wrap></sec></body>
<back><ref-list><ref><p>Imagination causes a false finding.</p></ref></ref-list></back></article>"""
    write_text_lf(source / "article.jats", xml)
    article = {"title": "Counter observations (synthetic example)", "year": 2023,
               "sections": [{"title": "Observations", "paragraphs": [
                   {"paragraph_id": "q42", "text": "Counter B did not increase noise."}]}],
               "tables": [{"label": "Trial", "columns": ["Counter", "Time (ms)"],
                           "rows": [["Counter B", "14.8"], ["Counter C", "11.2"]]}]}
    write_json_atomic(source / "measurements.json", article)
    case = {"case_id": "local-structured", "input_files": [{"file": "measurements.json"}, "article.jats"],
            "task": "The instructions increase confusion.", "output_contract": ["case_id", "nanopublications"],
            "materials": [{"title": "Inline fragment", "paragraphs": [
                {"id": "S1", "text": "Resonator C may induce drift."},
                {"id": "S2", "text": "The maximum width of Band C was 7.4 nm."}]}]}
    write_json_atomic(source / "case.json", case)
    manifest = ingest_input(source, work / "ingest", case_path=source / "case.json")
    papers = manifest["papers"]
    assert len(papers) == 3 and not manifest["uncertainties"]
    docs = [json.loads(Path(p["document_json"]).read_text(encoding="utf-8")) for p in papers]
    scripts = Path(__file__).parent

    def cli(name, *args):
        proc = subprocess.run([sys.executable, "-B", "-X", "utf8", str(scripts / name), *map(str, args)],
                              text=True, encoding="utf-8", capture_output=True)
        assert proc.returncode == 0, proc.stderr or proc.stdout

    manifest_path = work / "ingest" / "ingest_manifest.json"
    bundle_path = work / "nanopubs.json"
    cli("decompose_assertions.py", "--manifest", manifest_path, "--out", work)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    records = bundle["nanopublications"]
    assert len(records) == 6
    by_id = {r["provenance"]["location"].get("original_id"): r for r in records}
    p = by_id["r7"]
    assert p["assertion"]["subject"] == "Filter A" and p["assertion"]["object"] == "signal drift"
    assert p["assertion"]["modality"] == "hedged" and "Under low light" in p["assertion"]["conditions"]
    assert p["provenance"]["source_metadata"]["version"] == "v2"
    assert p["provenance"]["source_metadata"]["authors"] == ["A. Li"]
    q = by_id["q42"]
    assert q["provenance"]["location"]["json_pointer"] == "/sections/0/paragraphs/0/text"
    assert q["assertion"]["polarity"] == "negative"
    assert all(r["provenance"]["location"]["pdf_page"] is None for r in records)
    table = next(r for r in records if r["provenance"]["minimal_evidence_snippet"] == "11.2")
    assert table["provenance"]["location"]["json_pointer"] == "/tables/0/rows/1/1"

    target = next(p for p in papers if Path(p["source_file"]).name == "case.json")
    doc = json.loads(Path(target["document_json"]).read_text(encoding="utf-8"))
    width = next(b for b in doc["blocks"] if b["native_locator"]["original_id"] == "S2")
    new = {"block_id": width["block_id"], "quote": width["text"], "subject": "Band C", "predicate": "has_property",
           "object": "maximum width 7.4 nm", "statement": "Band C had a maximum width of 7.4 nm.",
           "evidence_type": "direct_textual", "confidence_score": 0.9, "confidence_basis": "S2 reports width and unit."}
    full = {target["paper_id"]: [by_id["S1"], new]}
    write_json_atomic(work / "review.json", full)
    cli("decompose_assertions.py", "--manifest", manifest_path, "--reviewed", work / "review.json", "--out", work)
    cli("serialize_nanopubs.py", "--nanopubs", bundle_path, "--out", work)
    cli("build_nanopub_result.py", "--case", source / "case.json", "--nanopubs", bundle_path,
        "--manifest", manifest_path, "--out", work / "result.json")
    result = json.loads((work / "result.json").read_text(encoding="utf-8"))
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    nq = (work / "nanopubs.nq").read_text(encoding="utf-8")
    check = validate_nanopubs(case, result, bundle, docs, nq)
    assert check["pass"], check["errors"]
    assert bundle["nanopub_count"] == 7 and "7.4" in nq and "example.optics" in nq
    assert not any(term in nq for term in ("Imagination", "confusion"))
    forged = copy.deepcopy(bundle)
    forged["nanopublications"][-1]["assertion"]["object"] = "maximum width 77 nm"
    assert not validate_nanopubs(case, result, forged, docs, nq)["gate_results"]["gate2_relation_or_contradiction"]
    forged = copy.deepcopy(bundle)
    record = next(r for r in forged["nanopublications"] if r["provenance"]["minimal_evidence_snippet"] == "11.2")
    record["provenance"]["location"]["row_index"] = 1
    assert not validate_nanopubs(case, result, forged, docs, nq)["gate_results"]["gate3_grounding_and_entity"]
    try:
        decompose_document(doc, load_relation_vocabulary(), [{**new, "quote": "A fabricated quotation."}])
    except ValueError as exc:
        assert "differs from source" in str(exc)
    else:
        raise AssertionError("An invented quotation was accepted.")
    return {"pass": True, "papers": len(papers), "verified_assertions": bundle["nanopub_count"],
            "source_types": ["JATS", "JSON", "inline"], "structured_cells": 3,
            "fabricated_quantity_rejected": True, "wrong_table_row_rejected": True, "invented_quote_rejected": True}
