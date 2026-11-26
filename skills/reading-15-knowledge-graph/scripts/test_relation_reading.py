"""Regressions using fictional optical bench papers, including rejection of false directed claims."""
import unittest

from build_kg import build_kg, make_review_plan
from evidence_claims import canon
from query_kg import execute_query


def make_paper(source_id, paragraphs):
    return {"source_id": source_id, "source_file": source_id + ".json", "title": source_id,
            "doi": "not_reported", "authors": [], "year": "not_reported", "status": "success",
            "location_type": "jats_paragraph_id", "paragraphs": [
                {"source_id": source_id, "paragraph_id": pid, "text": text, "section_title": "Results",
                 "location_type": "jats_paragraph_id", "pdf_page": None} for pid, text in paragraphs]}


class ReadingRelations(unittest.TestCase):
    def setUp(self):
        self.corpus = {"papers": [
            make_paper("bench", [
                ("p1", "Spectral drift was reduced by the reference filter."),
                ("p2", "The detector used a reference filter."),
                ("p3", "The detector did not improve precision."),
                ("p4", "The detector reduced jitter; whereas prior work suggests the prototype may increase noise."),
                ("p5", "Jitter was reduced by 20 percent."),
            ]),
            make_paper("monitor", [
                ("p1", "The reference filter increased repeatability."),
                ("p2", "The compact camera was compared with the reference camera."),
            ]),
        ]}
        self.kg = build_kg(self.corpus)

    def probe(self, **options):
        return execute_query(self.kg, "relation_evidence_probe", direction="forward", **options)

    def test_active_passive_and_direction(self):
        args = {"source_id": "bench", "relation": "decreases"}
        hit = self.probe(entity_a="reference filter", entity_b="Spectral drift", **args)
        self.assertEqual(hit["hit_count"], 1)
        self.assertTrue(hit["has_direct_observation"])
        self.assertEqual(self.probe(entity_a="Spectral drift", entity_b="reference filter", **args)["hit_count"], 0)
        self.assertEqual(self.probe(entity_a="reference filter", entity_b="Spectral drift",
                                    source_id="bench", relation="increases")["hit_count"], 0)

    def test_attribution_negation_and_type(self):
        facts = [e for e in self.kg["edges"] if e["relation_type"] != "instance_of"]
        self.assertEqual(len(facts), 6)
        self.assertFalse(any(e["paragraph_id"] in {"p3", "p5"} for e in facts))
        audit = execute_query(self.kg, "conflict_and_boundary_audit", source_id="bench")
        self.assertEqual([(b["paragraph_id"], b["category"]) for b in audit["boundary_evidence"]],
                         [("p3", "negative_statement")])
        compound = {e["relation_type"]: e["evidence_strength"] for e in facts if e["paragraph_id"] == "p4"}
        self.assertEqual(compound, {"decreases": "direct_author_evidence", "increases": "prior_work_citation"})
        self.assertEqual([n["entity_type"] for n in self.kg["nodes"] if n["label"] == "The detector"], ["Instrument"])
        hypothesis = self.probe(entity_a="prototype", entity_b="noise", source_id="bench")
        self.assertEqual(hypothesis["hit_count"], 1)
        self.assertFalse(hypothesis["has_direct_observation"])

    def test_review_only_and_source_index(self):
        plan = make_review_plan(self.corpus, self.kg, per_source=2)
        self.assertEqual([(p["source_id"], len(p["selected_for_review"])) for p in plan["sources"]],
                         [("bench", 2), ("monitor", 2)])
        self.assertEqual(len(plan["sources"][0]["section_index"]["Results"]), 5)
        sentence = self.corpus["papers"][0]["paragraphs"][0]["text"]
        claim = {"subject": "reference filter", "subject_type": "Material", "object": "Spectral drift",
                 "object_type": "Metric", "quote_snippet": sentence, "relation": "decreases",
                 "evidence_strength": "direct_author_evidence"}
        reviewed = build_kg(self.corpus, reviewed_claims={"mode": "reviewed_only",
                            "paragraphs": [{"source_id": "bench", "paragraph_id": "p1", "claims": [claim]}]})
        self.assertEqual(len(reviewed["edges"]), 1)
        self.assertEqual(reviewed["status"], "partial")
        self.assertEqual(reviewed["reviewed_paragraphs"][0]["accepted_claims"], 1)

    def test_term_normalization_preserves_symbols(self):
        self.assertEqual(canon("The reference-filter"), canon("a reference filter"))
        self.assertNotEqual(canon("α phase"), canon("β phase"))
        self.assertNotEqual(canon("Q+"), canon("Q-"))


if __name__ == "__main__":
    unittest.main()
