"""Input regressions for the evidence-bearing corpus. Run with python3 -B scripts/test_corpus_inputs.py."""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from ingest_corpus import ingest_corpus


class CorpusInputs(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "input"
        self.out = Path(tmp.name) / "work"
        self.root.mkdir()

    def put(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
        return path

    def read(self, case=None):
        return ingest_corpus(self.root, self.out, case_path=case)

    def test_nested_manifest_same_filenames_and_jats_ids(self):
        manifest = {"sources": [
            {"source_id": "north", "file": "north/full.json", "doi": "10.1/n"},
            {"source_id": "south", "file": "south/full.json", "doi": "10.1/s"},
        ]}
        self.put("bundle/corpus-manifest.json", manifest)
        # Identical paragraph text in two independently identified works is not proof of identity.
        for side in ("north", "south"):
            self.put(f"bundle/{side}/full.json", {"paragraphs": [{"id": "p2", "text": "Samples shared this protocol."}]})
        self.put("bundle/unrelated.md", "Background attachment.")
        corpus = self.read()
        self.assertEqual(corpus["paper_count"], 2)
        for paper in corpus["papers"]:
            side = paper["source_id"]
            self.assertEqual(paper["source_file"], f"bundle/{side}/full.json")
            self.assertEqual(paper["doi"], "10.1/" + side[0])
            para, = paper["paragraphs"]
            self.assertEqual((para["source_id"], para["paragraph_id"], para["location_type"], para["pdf_page"]),
                             (side, "p2", "jats_paragraph_id", None))

    def test_aggregate_fills_only_its_own_missing_source(self):
        self.put("set/corpus-manifest.json", {"sources": [
            {"source_id": "a", "file": "a.json", "doi": "10.1/a"},
            {"source_id": "b", "file": "missing.json", "doi": "10.1/b"},
            {"source_id": "c", "file": "also-missing.json"},
        ]})
        self.put("set/a.json", {"paragraphs": [{"id": "p1", "text": "A measured a response."}]})
        self.put("set/material.json", {"doi": "10.1/a", "paragraphs": [
            {"source_id": "b", "id": "p1", "text": "B measured another response."},
            {"source_id": "a", "id": "p1", "text": "A measured a response."},
        ]})
        papers = {paper["source_id"]: paper for paper in self.read()["papers"]}
        self.assertEqual(papers["b"]["doi"], "10.1/b")
        self.assertEqual([p["text"] for p in papers["b"]["paragraphs"]], ["B measured another response."])
        self.assertEqual(papers["c"]["status"], "unverifiable")

    def test_docx_jats_and_explicit_selection(self):
        doc = self.root / "nested" / "target.docx"
        doc.parent.mkdir()
        with ZipFile(doc, "w") as archive:
            archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Cloud cover was measured.</w:t></w:r></w:p></w:body></w:document>')
        self.put("nested/second.xml", '<article xmlns="urn:jats"><body><p id="j42">The sensor recorded wind.</p></body></article>')
        self.put("distractor.txt", "Not in the case inventory.")
        case = self.put("case.json", {"input_files": [{"path": "nested/target.docx"}, "nested/second.xml", "missing.txt"]})
        corpus = self.read(case)
        self.assertEqual(corpus["missing_input_files"], ["missing.txt"])
        paragraphs = {p["paragraph_id"]: p for paper in corpus["papers"] for p in paper["paragraphs"]}
        self.assertEqual(set(paragraphs), {"j42", "p1"})
        self.assertEqual(paragraphs["j42"]["location_type"], "jats_paragraph_id")
        self.assertTrue(all(p["pdf_page"] is None for p in paragraphs.values()))

    def test_broken_paper_and_normalized_doi(self):
        self.put("papers/a.json", {"doi": "https://doi.org/10.1/A", "paragraphs": ["One paragraph."]})
        self.put("papers/b.json", {"doi": "10.1/a", "paragraphs": ["Another rendering."]})
        self.put("papers/c.json", "{")
        corpus = self.read()
        self.assertEqual([p["status"] for p in corpus["papers"]], ["success", "unverifiable"])
        self.assertEqual(len(corpus["duplicate_formats_skipped"]), 1)

    def test_standalone_aggregate_preserves_source_owners(self):
        self.put("data/material.json", {"source_id": "first", "doi": "10.1/a", "paragraphs": [
            {"source_id": "first", "id": "p1", "text": "Temperature rose."},
            {"source_id": "second", "id": "p1", "text": "Temperature was stable."},
        ]})
        papers = {p["source_id"]: p for p in self.read()["papers"]}
        self.assertEqual(set(papers), {"first", "second"})
        self.assertEqual((papers["first"]["doi"], papers["second"]["doi"]), ("10.1/a", "not_reported"))
        self.assertEqual(papers["second"]["paragraphs"][0]["text"], "Temperature was stable.")


if __name__ == "__main__":
    unittest.main()
