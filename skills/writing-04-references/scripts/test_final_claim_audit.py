"""Controlled checks of the final audit with real synchronization, QA and clause extraction."""
from pathlib import Path
import tempfile
import unittest

from claim_coverage import analyze_claim_coverage
from dedup_references import deduplicate_library
from final_claim_audit import final_claim_audit
from parse_bib_sources import make_raw_library, parse_json_records
from sync_manuscript_citations import sync_manuscript
from verify_citations import verify_citations


def run(draft, review_claim, source_quote, *, source_key="A", second=None, decisions=(), root=None):
    def source(key, abstract):
        return {"id":key, "type":"article-journal", "title":"Catalyst study "+key, "author":[{"family":"Park","given":"J"}],
                "year":2024, "container-title":"Teaching Journal", "abstract":abstract}
    library = [source("A",source_quote)]
    if second:
        library.append(source("B",second))
    records = parse_json_records(library,"references.json")
    raw = make_raw_library(records, ["references.json"], [])
    canonical, dedup = deduplicate_library(raw)
    sync = sync_manuscript(draft,canonical)
    ref = sync["citation_map"]["in_text_occurrences"][0]
    review = dict(line=ref["line"], occurrence_index=1, cite_key=source_key, claim_quote=review_claim,
                  verdict="supported", source_field="abstract", source_quote=source_quote, reason="Read the cited passage.")
    qa = verify_citations(sync["synced_manuscript"],sync["citation_map"],canonical,dedup,raw,semantic_reviews=[review])
    coverage = analyze_claim_coverage(sync["synced_manuscript"],canonical,sync["citation_map"],manuscript_source="draft.md")
    return final_claim_audit(coverage,qa,resolutions=decisions,evidence_root=root),coverage,qa


class FinalAuditTests(unittest.TestCase):
    def test_actual_contradiction_and_unresolved_external_attribution(self):
        claim = "Catalyst stability decreased by 40%"
        source = "Catalyst stability increased by 4%."
        draft = claim+" [@A]. An independent study reported selectivity rose by 50%."
        audit, _, qa = run(draft,claim,source)
        self.assertTrue(qa["claim_evidence"][0]["quote_located"])
        self.assertEqual(audit["summary"]["blocking_by_code"],
                         {"reviewed_quote_conflict":1, "unresolved_external_quantitative_claim":1})
        issue = audit["blocking"][0]
        self.assertEqual({i["kind"] for i in issue["conflicts"]},{"quantity","direction"})
        self.assertEqual((issue["cite_key"], issue["occurrence_index"]),("A",1))

    def test_correction_real_source_and_soft_queue(self):
        claim = "Catalyst stability increased by 4%"
        audit, _, _ = run(claim+" [@A]. Selectivity rose by 50%.",claim,claim+".")
        self.assertTrue(audit["passed"])
        self.assertEqual(audit["review_queue"][0]["code"],"possible_missing_citation")

    def test_distinct_condition_relation_unit_and_rounding(self):
        pairs = [
            ("Catalyst stability increased by 7% in adults","Catalyst stability decreased by 9% in children"),
            ("Catalyst stability increased by 7%","Catalyst stability increased to 9%"),
            ("Catalyst stability increased by 7%","Catalyst stability increased by 9 percentage points"),
            ("Catalyst stability increased by 7%","Catalyst stability increased by 7.2%"),
            ("Catalyst selectivity increased by 7%","Catalyst stability increased by 9%"),
            ("Catalyst stability increased by about 7%","Catalyst stability increased by 9%"),
            ("Catalyst stability increased from 8% to 16%","Catalyst stability rose by 8%"),
            ("Catalyst stability increased by 7%","Catalyst stability decreased by 9% while conversion increased by 20%"),
            ("Catalyst stability reportedly improved by 7%","The catalyst remained more stable in the treated group"),
        ]
        for claim, source in pairs:
            with self.subTest(claim=claim,source=source):
                self.assertTrue(run(claim+" [@A].",claim,source)[0]["passed"])

    def test_candidates_are_not_selected_evidence(self):
        claim = "Catalyst stability increased by 5%"
        audit, _, _ = run(claim+" [@A].",claim,claim+".",second="Catalyst stability decreased by 40%.")
        self.assertTrue(audit["passed"])
        # No semantic review can be verified against the wrong key.
        audit, _, qa = run(claim+" [@A].",claim,claim+".",source_key="B",second="Catalyst stability decreased by 40%.")
        self.assertFalse(qa["passed"])
        self.assertNotIn("reviewed_quote_conflict",audit["summary"]["blocking_by_code"])

    def test_chinese_and_negated_direction(self):
        audit, _, _ = run("催化剂稳定性未提高了4% [@A]。","催化剂稳定性未提高了4%","催化剂稳定性提高了4%。")
        self.assertEqual(audit["blocking"][0]["conflicts"][0]["kind"],"direction")

    def test_resolution_policies(self):
        source = "Catalyst stability increased by 4%"
        draft = source+" [@A]. Previous studies reported catalyst stability increased by 4%."
        _, coverage, _ = run(draft,source,source)
        clause = coverage["clauses"][1]
        resolution = {"claim_id":clause["id"],"claim_quote":clause["quote"],"decision":"shared_citation",
                      "occurrence_index":1,"cite_key":"A","source_quote":source,"reason":"The adjacent marker cites this exact result."}
        valid = run(draft,source,source,decisions=[resolution])[0]
        self.assertTrue(valid["passed"])
        self.assertEqual(len(valid["resolved"]),1)
        for override in ({"decision":"not_required"},{"decision":"insufficient_evidence"},{"source_quote":"A fabricated excerpt."},
                         {"occurrence_index":2},{"occurrence_index":[]},{"claim_quote":"Absent manuscript phrase"},{"reason":""}):
            with self.subTest(override=override):
                self.assertFalse(run(draft,source,source,decisions=[dict(resolution,**override)])[0]["passed"])

    def test_own_result_needs_an_independent_input(self):
        source = "Catalyst stability increased by 4%"
        draft = source+" [@A]. Conversion increased by 8%."
        _, coverage, _ = run(draft, source, source)
        item = {"claim_id":coverage["clauses"][1]["id"],"claim_quote":"Conversion increased by 8%",
                "source_quote":"Conversion increased by 8%", "decision":"own_result","source_file":"measurements.txt",
                "reason":"The supplied measurements record this result."}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/"measurements.txt").write_text(item["source_quote"],encoding="utf-8")
            result = run(draft,source,source,decisions=[item],root=root)[0]
            self.assertEqual(len(result["resolved"]),1)
            for change in ({"source_file":"draft.md"},{"source_file":"../draft.md"},{"source_quote":"Conversion was 90%"}):
                result = run(draft,source,source,decisions=[dict(item,**change)],root=root)[0]
                self.assertFalse(result["resolved"])

    def test_short_review_anchor_and_explicit_material_gap(self):
        claim = "Catalyst stability decreased by 40%"
        source = "Catalyst stability increased by 4%."
        draft = claim+" [@A]. Conversion rose by 8%."
        audit, cov, _ = run(draft, "Catalyst stability", source)
        self.assertIn("reviewed_quote_conflict", audit["summary"]["blocking_by_code"])
        gap = {"claim_id":cov["clauses"][1]["id"], "claim_quote":"Conversion rose by 8%",
               "decision":"insufficient_evidence", "reason":"The raw conversion series is absent."}
        audit, _, _ = run(draft, "Catalyst stability", source, decisions=[gap])
        self.assertIn("acknowledged_citation_evidence_gap", audit["summary"]["blocking_by_code"])


if __name__ == "__main__":
    unittest.main()
