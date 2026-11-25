#!/usr/bin/env python3
"""Run focused regressions for the clause audit, including the public DOCX CLI."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from claim_coverage import analyze_claim_coverage

LIB = {"records": [{"cite_key": "Pilot", "title": "Binding yield and catalyst stability",
                    "abstract": "Catalyst stability increased by 8% in the pilot. Binding yield increased by 18%."}]}
DRAFT = "Previous studies reported binding yield increased by 18%, whereas catalyst stability decreased by 4% [1]. Our setup used 10 mg."
MAP = {"in_text_occurrences": [{"line": 1, "original_tag": "[@Pilot]", "rendered_tag": "[1]", "cite_keys": ["Pilot"]}]}


class AuditTests(unittest.TestCase):
    def test_clause_scope_quantities_and_directions(self):
        audit = analyze_claim_coverage(DRAFT, LIB, MAP)
        missing, cited, own = audit["clauses"]
        self.assertEqual(missing["triage"], "inspect_missing_citation")
        self.assertEqual(missing["insertion_anchor"]["target_phrase"], DRAFT[:DRAFT.index(",")])
        self.assertEqual(missing["adjacent_marker_on_line"]["occurrence_index"], 1)
        self.assertEqual(cited["markers"][0]["cite_keys"], ["Pilot"])
        candidate = cited["source_candidates"][0]
        self.assertEqual({item["type"] for item in candidate["checks_for_reader"]},
                         {"different_values_same_unit", "compare_direction_or_negation"})
        self.assertEqual(own["triage"], "author_result_or_method")
        self.assertNotIn("adjacent_marker_on_line", own)
        for row in audit["clauses"]:
            self.assertEqual(row["quote"], DRAFT[row["start_column"]-1:row["end_column_inclusive"]])

    def test_decimal_abbreviations_and_mixed_units(self):
        draft = "A. Smith et al. reported catalyst stability rose by 8.0 percentage points [1]."
        audit = analyze_claim_coverage(draft, LIB, MAP)
        self.assertEqual(len(audit["clauses"]), 1)
        screens = [c for s in audit["clauses"][0]["source_candidates"] for c in s["checks_for_reader"]]
        self.assertEqual(screens, [])

    def test_semantic_paraphrase_and_multiple_values(self):
        draft = "Catalyst stability was higher by 8% and 14% [1]."
        row = analyze_claim_coverage(draft, LIB, MAP)["clauses"][0]
        checks = row["source_candidates"][0]["checks_for_reader"]
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0]["claim_values_missing_from_passage"], ["14"])
        draft = "该组首次观察到催化性能提升 [1]。"
        cross_script = analyze_claim_coverage(draft, LIB, MAP)["clauses"][0]
        self.assertFalse(any(p["checks_for_reader"] for p in cross_script["source_candidates"]))

    def test_source_identity_appendix_code_and_refs(self):
        draft = "~~~\\nPrevious studies reported a change of 15%.\\n~~~\\n## References\\n[1] Previous studies reported 8%.\\n## Appendix\\nCatalyst stability was higher by 8% [1].".replace("\\n", "\n")
        mapping = {"in_text_occurrences": [dict(MAP["in_text_occurrences"][0], line=7)]}
        passages = [{"source_file": "paper.pdf", "page": 1, "text": LIB["records"][0]["title"]},
                    {"source_file": "paper.pdf", "page": 4, "text": "Catalyst stability increased by 8% after cooling."},
                    {"source_file": "other.pdf", "page": 1, "text": "Other paper\nReferences\nBinding yield and catalyst stability\nCatalyst stability increased by 99%."}]
        audit = analyze_claim_coverage(draft, LIB, mapping, evidence_passages=passages)
        self.assertEqual(len(audit["clauses"]), 1)
        hits = audit["clauses"][0]["source_candidates"]
        self.assertTrue(any(h.get("source_file") == "paper.pdf" and h["page"] == 4 for h in hits))
        self.assertFalse(any(h.get("source_file") == "other.pdf" for h in hits))
        self.assertEqual(audit["clauses"][0]["section"], "Appendix")

    def test_docx_cli_paragraphs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            xml = ET.Element(ns + "document")
            body = ET.SubElement(xml, ns + "body")
            for text in ("Results", DRAFT):
                p = ET.SubElement(body, ns+"p")
                ET.SubElement(ET.SubElement(p,ns+"r"),ns+"t").text = text
            docx = root / "draft.docx"
            with ZipFile(docx, "w") as doc:
                doc.writestr("word/document.xml", ET.tostring(xml))
            for name, data in (("lib",LIB),("map",{"in_text_occurrences":[dict(MAP["in_text_occurrences"][0],line=2)]})):
                (root/name).write_text(json.dumps(data),encoding="utf-8")
            subprocess.run([sys.executable, "-B", str(Path(__file__).with_name("claim_coverage.py")), "--manuscript", str(docx),
                            "--lib", str(root/"lib"), "--map", str(root/"map"), "--out", str(root/"audit")], check=True,
                            capture_output=True)
            data = json.loads((root/"audit").read_text(encoding="utf-8"))
            candidate = next(r for r in data["clauses"] if r["triage"] == "inspect_missing_citation")
            self.assertEqual((candidate["word_paragraph"],candidate["line"]),(2,2))

    def test_wrapped_markdown_and_duplicate_marker_text(self):
        draft = "Catalyst stability reportedly decreased by\n4% [1].\n\nThe code example is `[1]`. Catalyst stability rose [1]."
        mapping = {"in_text_occurrences": [dict(MAP["in_text_occurrences"][0], line=2),
                                           dict(MAP["in_text_occurrences"][0], line=4)]}
        audit = analyze_claim_coverage(draft, LIB, mapping)
        cited = [r for r in audit["clauses"] if r["triage"] == "cited_scope"]
        self.assertEqual((cited[0]["line"], cited[0]["end_line"], cited[0]["markers"][0]["line"]), (1, 2, 2))
        self.assertEqual(cited[0]["quote"], draft.split("\n\n")[0])
        self.assertIn("Catalyst stability rose", cited[-1]["quote"])
        self.assertEqual(audit["unlocated_occurrence_indices"], [])


if __name__ == "__main__":
    unittest.main()
