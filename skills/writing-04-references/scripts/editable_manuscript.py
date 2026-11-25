"""Project synchronized citations onto a Word manuscript and render the actual file."""

from __future__ import annotations

import copy
import difflib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = "{" + NS + "}"
SPACE = "{http://www.w3.org/XML/1998/namespace}space"
REF_HEADING = re.compile(
    r"^(?:#{1,6}\s*)?(?:\d+(?:\.\d+)*[.、]?\s+)?(?:references?(?: cited)?|bibliography|引用文献|参考文献)"
    r"(?:\s*[(（]references?[)）])?\s*[:：]?$", re.I
)
BACK_MATTER = re.compile(
    r"^(?:#{1,6}\s+|Appendix\b|Appendices\b|Supplement\b|Funding\b|Acknowledg[e]?ments?\b|"
    r"Data availability\b|Author contributions\b|Conflict of interest\b|附录|致谢|基金|利益冲突)", re.I
)


def _text(p):
    return "".join(
        node.text or "" if node.tag == W + "t" else "\t" if node.tag == W + "tab" else "\n"
        for node in p.iter() if node.tag in (W + "t", W + "tab", W + "br", W + "cr")
    )


def _replace_run_text(p, desired):
    """Change only differing characters in existing runs, retaining formatting elsewhere."""
    old = _text(p)
    if old == desired:
        return
    protected = ("fldChar", "fldSimple", "instrText", "ins", "del", "tab", "br", "cr", "hyperlink")
    if any(p.find(".//" + W + tag) is not None for tag in protected):
        raise ValueError("edited paragraph contains a live field, revision, hyperlink or inline break")
    nodes = list(p.iter(W + "t"))
    if not nodes:
        raise ValueError("edited paragraph has no replaceable text")
    offsets = []
    pos = 0
    for node in nodes:
        offsets.append((pos, pos + len(node.text or "")))
        pos = offsets[-1][1]
    # Apply from the right so original offsets stay valid.
    edits = difflib.SequenceMatcher(None, old, desired, autojunk=False).get_opcodes()
    for op, start, end, a, b in reversed(edits):
        if op == "equal":
            continue
        owner = next((i for i, (_, finish) in enumerate(offsets) if finish > start), len(nodes) - 1)
        for i in range(owner, len(nodes)):
            left, right = offsets[i]
            if i > owner and left >= end:
                break
            t = nodes[i].text or ""
            before = t[:max(0, start - left)]
            after = t[min(right - left, max(0, end - left)):]
            nodes[i].text = before + (desired[a:b] if i == owner else "") + after
            nodes[i].set(SPACE, "preserve")
    if _text(p) != desired:
        raise ValueError("post-edit Word paragraph text disagrees with synchronized text")


def _paragraph(etree, value, template=None):
    p = etree.Element(W + "p")
    if template is not None:
        props = template.find(W + "pPr")
        if props is not None:
            props = copy.deepcopy(props)
            for tag in ("sectPr", "numPr", "pPrChange"):
                for child in props.findall(W + tag):
                    props.remove(child)
            p.append(props)
    run = etree.SubElement(p, W + "r")
    t = etree.SubElement(run, W + "t")
    t.set(SPACE, "preserve")
    t.text = value
    return p


def _lines(text):
    return [line for line in text.splitlines() if line.strip()]


def _plain_heading(text):
    return re.sub(r"^#{1,6}\s+", "", text.strip())


def _project(source, original, synced, bibliography, destination):
    from lxml import etree

    with zipfile.ZipFile(source) as inputs:
        root = etree.fromstring(inputs.read("word/document.xml"), etree.XMLParser(resolve_entities=False))
        body = root.find(W + "body")
        paragraphs = [p for p in body.iter(W + "p") if _text(p).strip()]
        original_lines = [_text(p) for p in paragraphs]
        if [s.strip() for s in original_lines] != [s.strip() for s in _lines(original)]:
            raise ValueError("visible source paragraphs could not be aligned with extracted manuscript")
        if original.strip() == synced.strip():
            shutil.copyfile(source, destination)
            return
        heading = next((i for i, s in enumerate(original_lines) if REF_HEADING.fullmatch(s.strip())), len(paragraphs))
        updated = _lines(synced)
        new_heading = next((i for i, s in enumerate(updated) if REF_HEADING.fullmatch(s.strip())), len(updated))
        entries = _lines(bibliography)
        if new_heading == len(updated) or updated[new_heading + 1:new_heading + 1 + len(entries)] != entries:
            raise ValueError("formatted bibliography cannot be identified in the edited Word text")
        edited_body = updated[:new_heading] + updated[new_heading + 1 + len(entries):]
        last_ref = heading + len(paragraphs) - len(edited_body)
        if not min(heading + 1, len(paragraphs)) <= last_ref <= len(paragraphs):
            raise ValueError("source and edited body paragraph counts differ")
        old_entries = paragraphs[heading + 1:last_ref]
        if any(BACK_MATTER.match(_text(p).strip()) for p in old_entries):
            raise ValueError("source back matter would be deleted with the bibliography")
        for p, line in zip(paragraphs[:heading] + paragraphs[last_ref:], edited_body):
            _replace_run_text(p, line)
        if heading == len(paragraphs):
            position = next((i for i, child in enumerate(body) if child.tag == W + "sectPr"), len(body))
            head_p = _paragraph(etree, _plain_heading(updated[new_heading]))
            body.insert(position, head_p)
        else:
            head_p = paragraphs[heading]
            if head_p.getparent() is not body:
                raise ValueError("bibliography heading is nested inside a table or content control")
            _replace_run_text(head_p, _plain_heading(updated[new_heading]))
            if heading != new_heading:
                if new_heading != len(edited_body) or head_p.find(".//" + W + "sectPr") is not None:
                    raise ValueError("cannot relocate a bibliography across Word section boundaries")
                # The synchronized manuscript placed the bibliography after its appendix.
                body.remove(head_p)
                position = next((i for i, child in enumerate(body) if child.tag == W + "sectPr"), len(body))
                body.insert(position, head_p)
        template = old_entries[0] if old_entries else None
        for p in old_entries:
            if p.getparent() is not body or any(
                p.find(".//" + W + tag) is not None for tag in
                ("drawing", "object", "sectPr", "fldChar", "fldSimple", "bookmarkStart", "bookmarkEnd", "commentRangeStart")
            ):
                raise ValueError("marked-up bibliography requires review in the source editor")
            body.remove(p)
        position = body.index(head_p) + 1
        for i, line in enumerate(entries):
            body.insert(position + i, _paragraph(etree, line, template))
        actual = [_text(p).strip() for p in body.iter(W + "p") if _text(p).strip()]
        expected = [s.strip() for s in updated]
        expected[new_heading] = _plain_heading(expected[new_heading])
        if actual != expected:
            raise ValueError("final DOCX read-back did not match synchronized manuscript")
        with zipfile.ZipFile(destination, "w") as result:
            for info in inputs.infolist():
                data = etree.tostring(root, encoding="UTF-8", xml_declaration=True) if info.filename == "word/document.xml" else inputs.read(info)
                result.writestr(info, data)


def _clean_docx(synced, destination):
    """Build a valid editable text edition even on standard-library-only Python."""
    ET.register_namespace("w", NS)
    document = ET.Element(W + "document")
    body = ET.SubElement(document, W + "body")
    for line in synced.splitlines():
        is_heading = bool(re.match(r"^#{1,6}\s+", line))
        p = _paragraph(ET, _plain_heading(line) if is_heading else line)
        if is_heading:
            r = p.find(W + "r")
            props = ET.Element(W + "rPr")
            ET.SubElement(props, W + "b")
            ET.SubElement(props, W + "sz", {W + "val": "28"})
            r.insert(0, props)
        body.append(p)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as out:
        out.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        out.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        out.writestr("word/document.xml", ET.tostring(document, encoding="utf-8", xml_declaration=True))


def _word_pdf(docx, pdf):
    import win32com.client

    word = win32com.client.DispatchEx("Word.Application")
    try:
        word.Visible = False
        word.DisplayAlerts = 0
        word.AutomationSecurity = 3
        word.Options.UpdateLinksAtOpen = False
        doc = word.Documents.Open(str(docx.resolve()), ReadOnly=True, AddToRecentFiles=False)
        try:
            doc.ExportAsFixedFormat(str(pdf.resolve()), 17)
        finally:
            doc.Close(False)
    finally:
        word.Quit(False)


def _render(docx, stage):
    pdf = docx.with_suffix(".pdf")
    office = shutil.which("libreoffice") or shutil.which("soffice")
    if office:
        profile = (stage / "lo-profile").resolve().as_uri()
        cmd = [office, "-env:UserInstallation=" + profile, "--headless", "--convert-to", "pdf", "--outdir", str(stage), str(docx)]
    elif os.name == "nt":
        cmd = [sys.executable, "-B", str(Path(__file__).resolve()), "--word-pdf", str(docx), str(pdf)]
    else:
        raise RuntimeError("LibreOffice executable was not found")
    outcome = subprocess.run(cmd, capture_output=True, timeout=75)
    if outcome.returncode or not pdf.is_file() or not pdf.read_bytes().startswith(b"%PDF-"):
        raise RuntimeError("Office renderer did not produce a readable PDF")
    return pdf


def _first_page(pdf, stage):
    png = stage / "synced_manuscript_page1.png"
    try:
        import fitz
    except ImportError:
        tool = shutil.which("pdftoppm")
        if not tool:
            return None
        subprocess.run([tool, "-f", "1", "-l", "1", "-singlefile", "-r", "140", "-png", str(pdf),
                        str(png.with_suffix(""))], capture_output=True, check=True, timeout=25)
    else:
        with fitz.open(pdf) as doc:
            doc[0].get_pixmap(dpi=140, alpha=False).save(png)
    return png


def export_docx(source: Path | None, original_text: str, synced_text: str, formatted_bibliography: str,
                output_dir: Path, *, render: bool = True, word_context=None) -> tuple[dict, list]:
    """Return existing artifact paths and localized limitations; leave the input untouched."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts, issues = {}, []
    with tempfile.TemporaryDirectory(dir=output_dir) as temp:
        stage = Path(temp).resolve()
        docx = stage / "synced_manuscript.docx"
        if source is not None and Path(source).suffix.lower() == ".docx":
            try:
                shadow = word_context.get("shadow") if word_context else None
                if word_context and (word_context["preserve_native"] or
                                     (shadow and original_text.strip() == synced_text.strip())):
                    shutil.copyfile(source, docx)
                    issues.append({"code": "word_original_retained", "detail": "Original fields, notes and caches require review in the native editor."})
                elif shadow:
                    from word_citations import restore_word_notes
                    projected = stage / "projected.docx"
                    _project(shadow, original_text, synced_text, formatted_bibliography, projected)
                    restore_word_notes(projected, docx, word_context)
                else:
                    _project(source, original_text, synced_text, formatted_bibliography, docx)
            except (ImportError, ValueError, KeyError, OSError, zipfile.BadZipFile, SyntaxError) as exc:
                if word_context and (word_context.get("shadow") or word_context.get("preserve_native")):
                    shutil.copyfile(source, docx)
                    issues.append({"code": "word_original_retained", "detail": str(exc),
                                   "review": "Review the synchronized text alongside the native document and its original fields."})
                else:
                    issues.append({"code": "docx_text_edition", "detail": str(exc),
                                   "review": "Compare the editable text edition with the input for layout, fields and objects."})
                    _clean_docx(synced_text, docx)
        else:
            _clean_docx(synced_text, docx)
        generated = {"editable_docx": docx}
        if render:
            try:
                pdf = _render(docx, stage)
                generated["preview_pdf"] = pdf
                png = _first_page(pdf, stage)
                if png is not None:
                    generated["preview_png"] = png
            except (OSError, RuntimeError, ValueError, IndexError, subprocess.SubprocessError) as exc:
                issues.append({"code": "docx_preview_incomplete", "detail": str(exc)})
        for key, path in generated.items():
            final = output_dir / path.name
            path.replace(final)
            artifacts[key] = str(final)
        # Files left by an older revision must not be mistaken for the current rendering.
        for key, name in (("preview_pdf", "synced_manuscript.pdf"), ("preview_png", "synced_manuscript_page1.png")):
            if key not in generated:
                (output_dir / name).unlink(missing_ok=True)
    return artifacts, issues


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--word-pdf":
        _word_pdf(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        raise SystemExit("Import export_docx from the reference pipeline.")
