"""Verify APA formatting in a rendered editable manuscript, using identical local inputs."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile
from xml.etree import ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
JOURNAL = "Review of Thermal Methods"
BOOK = "Field handbook"
URL = "https://doi.org/10.5555/apa-fixture"


def fixture(root):
    root.mkdir()
    records = [
        {"cite_key": "paper", "entry_type": "article", "authors": ["Picard, Jean-Luc"],
         "title": "Why do films drift?", "venue": JOURNAL, "year": 2021, "volume": "12",
         "issue": "3", "pages": "41--49", "doi": "10.5555/apa-fixture"},
        {"cite_key": "book", "entry_type": "book", "authors": ["National Research Institute"],
         "title": BOOK, "venue": "National Research Institute", "year": 2020},
    ]
    (root / "records.json").write_text(json.dumps(records), encoding="utf-8")
    (root / "draft.md").write_text("Film drift varies with temperature [@paper].\nField survey protocols [@book].\n", encoding="utf-8")
    (root / "case.json").write_text(json.dumps({"case_id": "synthetic_apa_editions", "style": "apa7",
                                                "manuscript_file": "draft.md", "bib_files": ["records.json"]}), encoding="utf-8")


def inspect(skill, root, output):
    proc = subprocess.run([sys.executable, "-B", str(skill / "scripts" / "build_reference_result.py"), "--docx",
                           "--input-dir", str(root), "--work-dir", str(output.parent / "work"), "--output-dir", str(output)],
                          capture_output=True, timeout=150, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    path = output / "result.json"
    if not path.exists():
        raise RuntimeError(proc.stderr.decode(errors="replace")[-1200:])
    result = json.loads(path.read_text(encoding="utf-8"))
    with zipfile.ZipFile(result["artifacts"]["editable_docx"]) as pkg:
        document = ET.fromstring(pkg.read("word/document.xml"))
        rel_file = "word/_rels/document.xml.rels"
        relationships = ({r.get("Id"): r.get("Target") for r in ET.fromstring(pkg.read(rel_file))}
                         if rel_file in pkg.namelist() else {})
    paragraphs = list(document.iter(W + "p"))
    def text(p):
        return "".join(x.text or "" for x in p.iter(W + "t"))
    article = next(p for p in paragraphs if JOURNAL in text(p))
    book = next(p for p in paragraphs if BOOK in text(p))
    heading = next(p for p in paragraphs if text(p) == "References")
    def italic(p):
        return [text(r) for r in p.iter(W + "r")
                if r.find(W + "rPr/" + W + "i") is not None and r.find(W + "rPr/" + W + "i").get(W + "val") != "0"]
    def attr(p, path, key):
        element = p.find("/".join(W + e for e in path.split("/")))
        return element.get(W + key) if element is not None else None
    hrefs = [relationships.get(link.get(R + "id")) for link in article.findall(W + "hyperlink")]
    pdf_italics = ""
    preview = result["artifacts"].get("preview_pdf")
    if preview:
        from pypdf import PdfReader
        styled = []
        def visit(value, cm, tm, font, size):
            # Word can draw slanted upright glyphs with the PDF text matrix when the font lacks italics.
            font_name = str((font or {}).get("/BaseFont", "")).lower()
            if abs(tm[2]) > 0.1 or "italic" in font_name or "oblique" in font_name:
                styled.append(value)
        for page in PdfReader(preview).pages:
            page.extract_text(visitor_text=visit)
        pdf_italics = " ".join(styled)
    return {"hyphenated_initials": "Picard, J.-L." in text(article),
            "title_punctuation": "drift? " in text(article),
            "page_range_en_dash": "41–49." in text(article),
            "journal_and_volume_only_italic": italic(article) == [JOURNAL, "12"],
            "book_title_italic": italic(book) == [BOOK],
            "group_publisher_omitted_when_author": text(book).count("National Research Institute") == 1,
            "bibliography_sorted": paragraphs.index(book) < paragraphs.index(article),
            "clickable_doi": hrefs == [URL],
            "hanging_half_inch": all(attr(p, "pPr/ind", "left") == attr(p, "pPr/ind", "hanging") == "720"
                                     for p in (article, book)),
            "double_spacing": all(attr(p, "pPr/spacing", "line") == "480" for p in (article, book)),
            "section_heading_centered_bold": attr(heading, "pPr/jc", "val") == "center" and
                                             any(r.find(W + "rPr/" + W + "b") is not None for r in heading.iter(W + "r")),
            "rendered_italics": JOURNAL in pdf_italics and BOOK in pdf_italics and "(3)" not in pdf_italics}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--before-skill", type=Path)
    p.add_argument("--skill", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--out-dir", type=Path, required=True)
    args = p.parse_args()
    args.out_dir.mkdir(exist_ok=True, parents=True)
    directory = Path(tempfile.mkdtemp(dir=args.out_dir, prefix="apa-")).resolve()
    root = directory / "inputs"
    fixture(root)
    checks = {k: inspect(v.resolve(), root, directory / k / "output") for k, v in
              (("before", args.before_skill), ("after", args.skill)) if v}
    report = directory / "comparison.json"
    report.write_text(json.dumps(checks, indent=2), encoding="utf-8")
    print(report, json.dumps({k: [sum(c.values()), len(c)] for k, c in checks.items()}))
    return int(not all(checks["after"].values()))


if __name__ == "__main__":
    raise SystemExit(main())
