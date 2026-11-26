"""Regression for title-page marginalia, plot ticks and sections following references."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import fitz

from parse_pdf import run_parse_pipeline


class PaperLayoutTest(unittest.TestCase):
    def test_title_figures_and_appendix(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "paper.pdf"
            with fitz.open() as pdf:
                page = pdf.new_page()
                page.insert_text((65, 86), "Monitoring an optical reference", fontsize=20)
                page.insert_text((18, 460), "arXiv:0000.00000v1", fontsize=26, rotate=90)
                page.insert_text((68, 121), "Robin Ellis\nUniversity of Example", fontsize=11)
                page.insert_text((65, 190), "1 Results", fontsize=14)
                page.insert_text((65, 222), "The monitor stabilized at a fixed current.", fontsize=11)
                page.insert_text((65, 271), "0.0 0.5 1.0 1.5 cycles", fontsize=7)
                page.insert_text((65, 360), "References", fontsize=14)
                page.insert_text((65, 394), "[1] Ellis. Optical monitoring.", fontsize=11)
                page = pdf.new_page()
                page.insert_text((65, 100), "2 Appendix", fontsize=14)
                page.insert_text((65, 135), "The derivation assumes a constant current.", fontsize=11)
                pdf.save(source)
            doc = run_parse_pipeline(source, root / "parsed", ocr_mode="off")
            self.assertEqual(doc["metadata"]["title"], "Monitoring an optical reference")
            self.assertIn("Robin Ellis", doc["metadata"]["authors"])
            self.assertFalse(any("cycles" in section["title"] for section in doc["sections"]))
            self.assertFalse(any(block["role"] == "heading" and "cycles" in block["text"]
                                 for block in doc["blocks"]))
            self.assertEqual([ref["page"] for ref in doc["references"]], [1])
            appendix = next(block for block in doc["blocks"] if "derivation" in block["text"])
            self.assertEqual(appendix["section_path"], ["2 Appendix"])
            self.assertNotEqual(appendix["role"], "reference")


if __name__ == "__main__":
    unittest.main()
