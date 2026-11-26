"""Offline contract, source identity, and scientific-answer regressions through the normal entry."""

from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import copy
import json
import unittest

from pdf_utils import write_json_atomic, write_text_lf
from run_case import run


class ReviewWorkflowTest(unittest.TestCase):
    def test_structured_sources_and_scoped_answers(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            article = {"sections": [{"title": "Experiment", "sections": [{"title": "Drift", "paragraphs": [
                {"id": "measure", "text": "Sensor A drifted by 0.43 nm and sensor B by 0.11 nm in dry air."}
            ]}]}]}
            case = {"case_id": "drift", "input_files": [{"path": "papers.json"}, {"path": "other.xml"}],
                    "output_contract": {"type": "object", "additionalProperties": False,
                    "required": ["case_id", "status", "answer"], "properties": {"case_id": {"type": "string"},
                    "status": {"type": "string"}, "answer": {"type": "object", "additionalProperties": False,
                    "required": ["difference"], "properties": {"difference": {"type": ["number", "null"]}}}}}}
            inputs = root / "input"
            write_json_atomic(inputs / "case.json", case)
            write_json_atomic(inputs / "papers.json", {"documents": [article]})
            write_text_lf(inputs / "other.xml", "<article><body><sec><title>Design</title><sec><title>Humidity</title>"
                          "<p id='lim'>Humid air was not tested.</p></sec></sec></body></article>")
            args = SimpleNamespace(stage="prepare", input=inputs, case=None, work=root / "work",
                                   out=root / "out", review=root / "review.json", ocr="off")
            self.assertEqual(run(args)["papers"], 2)
            packet = (args.work / "reader" / "paper_2.md").read_text(encoding="utf-8")
            self.assertIn("/article/body[1]/sec[1]/sec[1]/p[1]", packet)
            pointer = "/documents/0/sections/0/sections/0/paragraphs/0/text"
            evidence = {"paper_id": "paper_1", "element_id": pointer,
                        "quote_snippet": article["sections"][0]["sections"][0]["paragraphs"][0]["text"]}
            review = {"fields": {"answer": {"difference": 0.32}}, "field_evidence": {"/answer/difference": [evidence]},
                      "derivations": {"/answer/difference": {"operation": "difference", "operands": [0.43, 0.11],
                                                            "decimal_places": 2, "unit": "nm"}}}
            args.stage = "finish"
            write_json_atomic(args.review, review)
            self.assertTrue(run(args)["audit"])
            delivered = (args.out / "result.json").read_bytes()
            self.assertEqual(set(json.loads(delivered)), {"case_id", "status", "answer"})
            rows = json.loads((args.out / "notes" / "case_evidence.json").read_text(encoding="utf-8"))
            self.assertEqual(rows["/answer/difference"][0]["native_locator"]["original_id"], "measure")
            for change in ("wrong_paper", "unsupported_number", "unbound_claim", "contract", "id"):
                with self.subTest(change=change):
                    bad = copy.deepcopy(review)
                    if change == "wrong_paper":
                        bad["field_evidence"]["/answer/difference"][0]["paper_id"] = "paper_2"
                    elif change == "unsupported_number":
                        bad["fields"]["answer"]["difference"] = 1.32
                    elif change == "unbound_claim":
                        bad["fields"]["answer"]["new_claim"] = "The detector operated under water."
                    elif change == "id":
                        bad["fields"]["case_id"] = "unrelated"
                    else:
                        bad["fields"]["answer"]["extra"] = 0.43
                        bad["field_evidence"]["/answer/extra"] = [evidence]
                    write_json_atomic(args.review, bad)
                    with self.assertRaises(ValueError):
                        run(args)
                    self.assertEqual((args.out / "result.json").read_bytes(), delivered)
            missing = {"fields": {"answer": {"difference": None}},
                       "field_limits": {"/answer/difference": {"reason": "Dry-air results cannot establish a humid-air difference.",
                                                               "checked": ["paper_1/Drift", "paper_2/Humidity"]}}}
            write_json_atomic(args.review, missing)
            self.assertEqual(run(args)["status"], "partial")


if __name__ == "__main__":
    unittest.main()
