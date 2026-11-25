"""Editable OOXML output with run-level patching and a rendered Office preview."""

from __future__ import annotations

import difflib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from zipfile import ZipFile


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
XML = "{http://www.w3.org/XML/1998/namespace}"


def _parts(paragraph):
    """Yield visible tokens and the text nodes that can safely be changed."""
    def visit(node, in_field=False):
        if node.tag in (W + "del", W + "moveFrom", W + "drawing", W + "pict", W + "instrText"):
            return
        if node.tag in (M + "oMath", M + "oMathPara"):
            yield ("⟦" + "".join(e.text or "" for e in node.iter(M + "t")) + "⟧", None)
            return
        if node.tag == W + "t":
            yield (node.text or "", None if in_field else node)
            return
        if node.tag in (W + "br", W + "cr", W + "tab"):
            yield ("\t" if node.tag == W + "tab" else " ", None)
            return
        locked = in_field or node.tag in (W + "fldSimple", W + "sdt", W + "hyperlink")
        for child in node:
            yield from visit(child, locked)

    # Complex fields can span runs. Keep paragraphs containing them read-only in
    # the Office copy, since updating display results can corrupt the field.
    yield from visit(paragraph, any(True for _ in paragraph.iter(W + "fldChar")))


def _document(path):
    from lxml import etree
    with ZipFile(path) as package:
        xml = package.read("word/document.xml")
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    return etree.fromstring(xml, parser)


def _paragraphs(root):
    return root.xpath(
        ".//w:body//w:p[not(ancestor::w:p or ancestor::w:del or ancestor::w:moveFrom)]",
        namespaces={"w": W[1:-1]},
    )


def read_docx(path):
    root = _document(path)
    tables = {tbl: n for n, tbl in enumerate(root.iter(W + "tbl"), 1)}
    rows = []
    for n, paragraph in enumerate(_paragraphs(root), 1):
        text = "".join(t for t, _ in _parts(paragraph))
        loc = f"paragraph {n}"
        cell = next(paragraph.iterancestors(W + "tc"), None)
        if cell is not None:
            row = next(cell.iterancestors(W + "tr"))
            table = next(row.iterancestors(W + "tbl"))
            row_n = len(list(row.itersiblings(W + "tr", preceding=True))) + 1
            cell_n = len(list(cell.itersiblings(W + "tc", preceding=True))) + 1
            loc += f", table {tables[table]}, row {row_n}, cell {cell_n}"
        rows.append((text, {"location": loc, "paragraph": n}))
    return rows


def _patch_paragraph(paragraph, target):
    parts = list(_parts(paragraph))
    original = "".join(s for s, _ in parts)
    if original == target:
        return True
    owners = [n for n, (s, _) in enumerate(parts) for _ in s]
    result = [""] * len(parts)
    matcher = difflib.SequenceMatcher(None, original, target, autojunk=False)
    for op, a, b, c, d in matcher.get_opcodes():
        if op == "equal":
            for pos in range(a, b):
                result[owners[pos]] += original[pos]
            continue
        if any(parts[owner][1] is None for owner in set(owners[a:b])):
            return False
        if c != d:
            # For insertions next to a field, attach the text to an editable run.
            choices = [owners[i] for i in (a, a - 1) if 0 <= i < len(owners)]
            owner = next((i for i in choices if parts[i][1] is not None), None)
            if owner is None:
                return False
            result[owner] += target[c:d]
    for value, (old, node) in zip(result, parts):
        if node is not None and value != old:
            node.text = value
            if value.startswith(" ") or value.endswith(" "):
                node.set(XML + "space", "preserve")
    return True


def _patch_docx(original, target_file, materials, polished):
    from lxml import etree
    root = _document(original)
    paragraphs = _paragraphs(root)
    # Some callers add blank separator lines when reassembling sentences.
    targets = [line for line in polished.splitlines() if line.strip()]
    mappings = [(line, loc) for line, loc in zip(materials["text"].splitlines(), materials["line_map"])
                if line.strip()]
    if len(targets) != len(mappings):
        raise ValueError("The edited line count differs from the source DOCX paragraph map.")
    issues = []
    for (_, loc), target in zip(mappings, targets):
        if not _patch_paragraph(paragraphs[loc["paragraph"] - 1], target):
            issues.append({"code": "docx_protected_content", "source": loc["source"],
                           "location": loc["location"], "blocking": True,
                           "message": "This paragraph retained its original Word field or inline object. Review the text revision."})
    xml = etree.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True)
    with ZipFile(original) as src, ZipFile(target_file, "w") as dst:
        for item in src.infolist():
            dst.writestr(item, xml if item.filename == "word/document.xml" else src.read(item))
    return issues


def _word_preview(docx_path, pdf_path):
    import win32com.client
    word = win32com.client.DispatchEx("Word.Application")
    try:
        word.Visible = False
        word.DisplayAlerts = 0
        word.AutomationSecurity = 3
        word.Options.UpdateLinksAtOpen = False
        doc = word.Documents.Open(str(docx_path), ReadOnly=True, AddToRecentFiles=False)
        try:
            doc.ExportAsFixedFormat(str(pdf_path), 17)
        finally:
            doc.Close(False)
    finally:
        word.Quit()


def _render(docx_path, directory):
    renderer = shutil.which("libreoffice") or shutil.which("soffice")
    pdf = directory / (docx_path.stem + ".pdf")
    if renderer:
        profile = (directory / "profile").as_uri()
        cmd = [renderer, "-env:UserInstallation=" + profile, "--headless",
               "--convert-to", "pdf", "--outdir", str(directory), str(docx_path)]
    elif sys.platform == "win32":
        cmd = [sys.executable, str(Path(__file__).resolve()), "--word-preview", str(docx_path), str(pdf)]
    else:
        raise RuntimeError("No LibreOffice renderer found. Render the DOCX with the platform Office renderer.")
    subprocess.run(cmd, cwd=directory, check=True, timeout=60, capture_output=True)
    with pdf.open("rb") as data:
        if data.read(5) != b"%PDF-":
            raise ValueError("Renderer did not produce a PDF.")
    return pdf


def export_docx(input_dir: Path, materials: dict, polished_text: str, output_dir: Path):
    """Return actual artifact paths and any unresolved DOCX/rendering limitations."""
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "polished_manuscript.docx"
    preview_target = target.with_suffix(".pdf")
    issues = []
    artifacts = {}
    with tempfile.TemporaryDirectory(dir=output_dir) as scratch:
        temporary = Path(scratch)
        draft = temporary / target.name
        try:
            sources = materials["sources"]
            if len(sources) == 1 and Path(sources[0]).suffix.lower() == ".docx":
                issues.extend(_patch_docx(Path(input_dir) / sources[0], draft, materials, polished_text))
            else:
                from docx import Document
                doc = Document()
                for line in polished_text.splitlines():
                    if line.startswith("#") and line.lstrip("#").startswith(" "):
                        depth = min(len(line) - len(line.lstrip("#")), 9)
                        doc.add_heading(line.lstrip("#").lstrip(), level=depth)
                    else:
                        doc.add_paragraph(line)
                doc.save(draft)
            draft.replace(target)
            artifacts["polished_docx"] = str(target)
        except Exception as exc:
            target.unlink(missing_ok=True)
            preview_target.unlink(missing_ok=True)
            return {}, [{"code": "docx_export_failed", "message": str(exc), "blocking": True}]
        try:
            preview = _render(target, temporary)
            preview.replace(preview_target)
            artifacts["preview_pdf"] = str(preview_target)
        except Exception as exc:
            preview_target.unlink(missing_ok=True)
            issues.append({"code": "docx_preview_unavailable", "message": str(exc), "blocking": True})
    return artifacts, issues


if __name__ == "__main__" and len(sys.argv) == 4 and sys.argv[1] == "--word-preview":
    _word_preview(Path(sys.argv[2]), Path(sys.argv[3]))
