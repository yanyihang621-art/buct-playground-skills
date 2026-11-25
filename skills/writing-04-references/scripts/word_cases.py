"""Synthetic Word field and note fixtures. Can compare installed Skill directories via real CLIs."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from lxml import etree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
PKG = "{http://schemas.openxmlformats.org/package/2006/relationships}"
TYPES = "{http://schemas.openxmlformats.org/package/2006/content-types}"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
BOOK = {"vale": "Thermal drift in glass", "reed": "Cooling time in thin films", "north": "Stress in films"}


def _node(parent, name, text=None, **attrs):
    child = ET.SubElement(parent, W + name, {W + k: v for k, v in attrs.items()})
    child.text = text
    return child


def _run(p, text):
    run = _node(p, "r")
    t = _node(run, "t", text)
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    return run


def _complex(p, instruction, cache):
    _node(_node(p, "r"), "fldChar", fldCharType="begin")
    n = len(instruction) // 2
    for part in (instruction[:n], instruction[n:]):
        _node(_node(p, "r"), "instrText", part)
    _node(_node(p, "r"), "fldChar", fldCharType="separate")
    _run(p, cache)
    _node(_node(p, "r"), "fldChar", fldCharType="end")


def fixture(folder, unresolved=False, embedded=False, corrupt=False):
    from docx import Document
    folder.mkdir(parents=True)
    original = folder / "draft.docx"
    blank = folder.parent / "blank.docx"
    Document().save(blank)
    with zipfile.ZipFile(blank) as src:
        parts = {name: src.read(name) for name in src.namelist()}
    main = ET.fromstring(parts["word/document.xml"])
    body = main.find(W + "body")
    p = ET.Element(W + "p")
    body.insert(0, p)
    _run(p, "Thermal drift ")
    _complex(p, " CITATION vale ", "[99]")
    _node(_node(p, "r"), "footnoteReference", id="1")
    _run(p, " and stress ")
    en = ("<EndNote><Cite><record><titles><title>Stress in films</title></titles>"
          "<contributors><authors><author>North, A</author></authors></contributors><dates><year>2020</year></dates>"
          "<periodical><full-title>Synthetic Journal</full-title></periodical></record></Cite></EndNote>")
    _complex(p, " ADDIN EN.CITE " + en, "[88]")
    _node(_node(p, "r"), "endnoteReference", id="1")
    _run(p, " measured.")
    rels = ET.fromstring(parts["word/_rels/document.xml.rels"])
    types = ET.fromstring(parts["[Content_Types].xml"])
    for kind in ("footnote", "endnote"):
        root = ET.Element(W + kind + "s", nsmap={"w": W[1:-1]})
        for n, element in (("-1", "separator"), ("0", "continuationSeparator")):
            line = _node(root, kind, id=n, type=element)
            _node(_node(_node(line, "p"), "r"), element)
        item = _node(root, kind, id="1")
        para = _node(item, "p")
        _node(_node(para, "r"), kind + "Ref")
        if kind == "footnote":
            _run(para, " Cooling time ")
            zotero_item = {"id": 1234, "itemData": {"title": BOOK["reed"], "author": [{"family": "Reed", "given": "Ada"}],
                                                    "issued": {"date-parts": [[2020]]}, "container-title": "Synthetic Journal"}}
            if unresolved:
                zotero_item["locator"] = "7-9"
            instr = " ADDIN ZOTERO_ITEM CSL_CITATION " + json.dumps({"citationItems": [zotero_item]})
            field = _node(para, "fldSimple", instr=instr)
            _run(field, "[77]")
        else:
            _run(para, " Drift in glass [@vale].")
        name = kind + "s.xml"
        parts["word/" + name] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        ET.SubElement(rels, PKG + "Relationship", Id="rIdTest" + kind, Type=REL + kind + "s", Target=name)
        ET.SubElement(types, TYPES + "Override", PartName="/word/" + name,
                      ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml." + kind + "s+xml")
    if embedded:
        b = "{http://schemas.openxmlformats.org/officeDocument/2006/bibliography}"
        sources = ET.Element(b + "Sources", nsmap={"b": b[1:-1]})
        record = ET.SubElement(sources, b + "Source")
        for k, val in {"Tag": "vale", "Title": BOOK["vale"], "SourceType": "JournalArticle",
                       "JournalName": "Synthetic Journal", "Year": "2020"}.items():
            ET.SubElement(record, b + k).text = val
        writer = ET.SubElement(ET.SubElement(ET.SubElement(ET.SubElement(record, b + "Author"),
                               b + "Author"), b + "NameList"), b + "Person")
        ET.SubElement(writer, b + "Last").text = "Vale"
        ET.SubElement(writer, b + "First").text = "Ada"
        parts["customXml/item19.xml"] = ET.tostring(sources, encoding="utf-8", xml_declaration=True)
        ET.SubElement(rels, PKG + "Relationship", Id="rIdTestLibrary", Type=REL + "customXml", Target="../customXml/item19.xml")
    for name, xml in (("word/document.xml", main), ("word/_rels/document.xml.rels", rels), ("[Content_Types].xml", types)):
        parts[name] = ET.tostring(xml, encoding="utf-8", xml_declaration=True)
    if corrupt:
        parts["word/document.xml"] = b"<damaged"
    with zipfile.ZipFile(original, "w", zipfile.ZIP_DEFLATED) as pkg:
        for name, content in parts.items():
            pkg.writestr(name, content)
    if not embedded:
        (folder / "library.json").write_text(json.dumps([
            {"cite_key": k, "title": v, "authors": [k.title() + ", Ada"], "venue": "Synthetic Journal", "year": 2020}
            for k, v in BOOK.items()]), encoding="utf-8")
    (folder / "case.json").write_text(json.dumps({"case_id": "native_word_" + folder.name, "style": "gbt7714",
                                                 "manuscript_file": original.name, "bib_files": [] if embedded else ["library.json"]}), encoding="utf-8")


def _evaluate(skill, folder, output):
    command = [sys.executable, "-B", str(skill / "scripts" / "build_reference_result.py"),
               "--input-dir", str(folder), "--output-dir", str(output), "--work-dir", str(output.parent / "work")]
    proc = subprocess.run(command, capture_output=True, timeout=150, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    result_file = output / "result.json"
    if not result_file.exists():
        raise RuntimeError(proc.stderr.decode("utf-8", "replace")[-1500:])
    result = json.loads(result_file.read_text(encoding="utf-8"))
    if folder.name == "corrupt":
        return {"honest_refusal": result["status"] == "refused",
                "error_locatable": any("draft.docx" in json.dumps(row) for row in result.get("uncertainties", [])),
                "no_fabricated_editable": "editable_docx" not in result.get("artifacts", {})}
    if folder.name == "embedded":
        library = json.loads(Path(result["artifacts"]["raw_library_json"]).read_text(encoding="utf-8"))
        names = {r["title"] for r in library["records"]}
        refs = [p for r in library["records"] for p in r.get("source_provenance", [])]
        parts = {p.get("part") for p in refs}
        bib = result["artifacts"].get("bibliography_formatted")
        full = Path(bib).read_text(encoding="utf-8") if bib else ""
        return {"three_entries_from_docx": names == set(BOOK.values()),
                "located_in_three_parts": parts == {"customXml/item19.xml", "word/document.xml", "word/footnotes.xml"},
                "all_titles_in_synchronized_bibliography": all(title in full for title in BOOK.values())}
    word = Path(result["artifacts"]["editable_docx"])
    with zipfile.ZipFile(word) as pkg:
        roots = {n: ET.fromstring(pkg.read("word/" + n + ".xml")) for n in ("document", "footnotes", "endnotes")
                 if "word/" + n + ".xml" in pkg.namelist()}
    texts = {n: "".join(v.text or "" for v in r.iter(W + "t")) for n, r in roots.items()}
    codes = [i.get("code") for i in result["uncertainties"] if isinstance(i, dict)]
    if folder.name == "locator":
        return {"preserved_native_zip": (folder / "draft.docx").read_bytes() == word.read_bytes(),
                "qualified_unresolved_field": "word_field_unresolved" in codes,
                "partial": result["status"] == "partial"}
    pdf_text = ""
    if result["artifacts"].get("preview_pdf"):
        import fitz
        with fitz.open(result["artifacts"]["preview_pdf"]) as doc:
            pdf_text = " ".join(p.get_text() for p in doc)
    main = texts["document"]
    return {"first_body_cite": "Thermal drift [1]" in main, "note_before_later_body": "Cooling time [2]" in texts.get("footnotes", ""),
            "later_body_cite": "and stress [3]" in main, "endnote_alias": "Drift in glass [1]." in texts.get("endnotes", ""),
            "body_joined": "Thermal drift [1] and stress [3] measured." in main,
            "no_private_markers": all("⟦" not in value for value in texts.values()),
            "one_bibliography_per_key": all(main.count(title) == 1 for title in BOOK.values()),
            "stale_caches_gone": all(mark not in " ".join(texts.values()) for mark in ("[99]", "[88]", "[77]")),
            "native_note_anchors_preserved": all(len(list(roots["document"].iter(W + kind + "Reference"))) == 1
                                                 for kind in ("footnote", "endnote")),
            "render_shows_note": "Cooling time [2]" in pdf_text}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--before-skill", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(dir=args.out_dir, prefix="word-fields-")).resolve()
    cases = ("resolvable", "locator", "embedded", "corrupt")
    for case in cases:
        fixture(root / "inputs" / case, case == "locator", case == "embedded", case == "corrupt")
    summary = {}
    for version, skill in (("before", args.before_skill), ("after", args.skill)):
        if skill:
            summary[version] = {case: _evaluate(skill.resolve(), root / "inputs" / case, root / version / case / "output")
                                for case in cases}
    (root / "word_comparison.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(root, json.dumps(summary))
    return int(not all(all(m.values()) for m in summary["after"].values()))


if __name__ == "__main__":
    raise SystemExit(main())
