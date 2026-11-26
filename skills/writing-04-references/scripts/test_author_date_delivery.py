"""Exercise narrative attribution, source locators and a closed case contract via the normal entry point."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from build_reference_result import run_reference_pipeline

def record(key, author):
    return {"cite_key": key, "authors": ["Iris Vale", author], "year": 2024, "venue": "Teaching Notes",
            "title": "Study of " + key, "url": "https://example.org/" + key,
            "abstract": "A calibration check used three samples."}


def run(root, body, records, contract=None, review=None):
    source = root / "input"
    source.mkdir()
    data = {"case_id": "demo", "manuscript": body, "references": records, "style": "IEEE"}
    if contract:
        data["output_contract"] = contract
    (source / "case.json").write_text(json.dumps(data), encoding="utf-8")
    return run_reference_pipeline(source, root / "work", root / "output", review=review)["result"]


class CitationDelivery(unittest.TestCase):
    def test_coauthors_page_and_narrative(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manuscript = "Vale and Lin (2024, p. 5) reported three samples.\nA check followed (Vale & Ng, 2024, pp. 11–12)."
            result = run(root, manuscript, [record("Blue", "May Lin"), record("Orange", "Amy Ng")])
            text = Path(result["artifacts"]["synced_manuscript"]).read_text(encoding="utf-8")
            self.assertIn("Vale and Lin [1, p. 5] reported", text)
            self.assertIn("[2, pp. 11–12]", text)
            self.assertEqual(result["metrics"]["bibliography_count"], 2)

    def test_unresolved_second_author(self):
        with tempfile.TemporaryDirectory() as temp:
            result = run(Path(temp), "Example (Vale & Yen, 2024).", [record("Blue", "May Lin")])
            self.assertEqual(result["metrics"]["bibliography_count"], 0)
            draft = Path(result["artifacts"]["synced_manuscript"]).read_text(encoding="utf-8")
            self.assertIn("(Vale & Yen, 2024)", draft)

    def test_closed_contract_and_named_artifact(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            keys = ["case_id", "revised_file", "revised_manuscript"]
            contract = {"type": "object", "required": keys, "additionalProperties": False,
                        "properties": {k: {"type": "string", "minLength": 1} for k in keys}}
            review = {"output_names": {"synced_manuscript": "submission/updated.md"},
                      "contract_values": {"revised_file": "submission/updated.md"}}
            result = run(root, "Testing a claim [@Blue].", [record("Blue", "May Lin")], contract, review)
            delivered = json.loads((root / "output/result.json").read_text(encoding="utf-8"))
            self.assertEqual(set(delivered), set(keys))
            artifact = root / "output" / delivered["revised_file"]
            self.assertEqual(artifact.read_text(encoding="utf-8"), delivered["revised_manuscript"])
            self.assertTrue(Path(result["artifacts"]["manifest"]).is_file())
            self.assertTrue((root / "output/evidence/contract_audit.json").is_file())

    def test_unresolved_number_preserves_other_source_matches(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            library = [dict(record("Blue", "May Lin"), original_number=1, doi="10.1002/example.2024")]
            manuscript = ("The calibration used three samples [1].\nMissing citation [99].\n\nReferences\n"
                          "[1] Vale, I., & Lin, M. (2024). Study of Blue. DOI:10.1002/example.2024.\n\n"
                          "Appendix\nA second use [1].\n")
            result = run(root, manuscript, library,
                         review={"insert_claims": [{"target_phrase": "The calibration", "cite_keys": ["Blue"]}]})
            draft = Path(result["artifacts"]["synced_manuscript"]).read_text(encoding="utf-8")
            audit = json.loads(Path(result["artifacts"]["retained_citations_json"]).read_text(encoding="utf-8"))
            self.assertEqual(draft, manuscript)
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["metrics"]["retained_original_sites"], 2)
            self.assertEqual([site["line"] for site in audit], [1, 8])
            self.assertTrue(all(site["original_tag"] == "[1]" and site["sources"][0]["cite_key"] == "Blue"
                                for site in audit))

    def test_located_source_conflict_blocks_completed_status(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = "Catalyst stability increased by 4%."
            record_with_abstract = dict(record("Blue", "May Lin"), abstract=source)
            claim = "Catalyst stability decreased by 40%"
            semantics = [{"line": 1, "occurrence_index": 1, "cite_key": "Blue", "verdict": "supported",
                          "claim_quote": claim, "source_field": "abstract", "source_quote": source,
                          "reason": "Reviewed against the supplied paper."}]
            result = run(root, claim + " [@Blue].", [record_with_abstract],
                         review={"semantic_reviews": semantics})
            self.assertEqual(result["status"], "partial")
            self.assertFalse(result["verification"]["passed"])
            self.assertIn("claim_coverage_and_factual_consistency", result["verification"]["failed_checks"])


if __name__ == "__main__":
    unittest.main()
