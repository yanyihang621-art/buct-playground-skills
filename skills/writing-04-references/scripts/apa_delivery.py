"""Apply APA 7 reference typography to the synchronized Word package before rendering."""
from __future__ import annotations

import copy
import re
import tempfile
import zipfile
from pathlib import Path

from format_references import apa_entries

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
PKG = "{http://schemas.openxmlformats.org/package/2006/relationships}"
RELS = "word/_rels/document.xml.rels"
SPACE = "{http://www.w3.org/XML/1998/namespace}space"


def _prop(parent, tag, **attrs):
    from lxml import etree
    child = parent.find(W + tag)
    if child is None:
        child = etree.SubElement(parent, W + tag)
    for key, val in attrs.items():
        child.set(W + key, val)
    return child


def _ppr(p):
    from lxml import etree
    props = p.find(W + "pPr")
    if props is None:
        props = etree.Element(W + "pPr")
        p.insert(0, props)
    return props


def _text(p):
    return "".join(t.text or "" for t in p.iter(W + "t"))


def _format_entry(p, parts, rels, targets):
    from lxml import etree
    # The projection creates a pure text bibliography. Leave custom fields or live objects intact.
    hyperlinks = {text for role, text in parts if role == "link"}
    for child in p:
        if child.tag == W + "pPr":
            continue
        if child.tag == W + "hyperlink" and _text(child) in hyperlinks:
            runs = list(child)
        else:
            runs = [child]
        if any(r.tag != W + "r" or any(el.tag not in (W + "rPr", W + "t") for el in r) for r in runs):
            raise ValueError("bibliography entry contains a field or embedded object")
    base = p.find(W + "r/" + W + "rPr")
    for child in list(p):
        if child.tag != W + "pPr":
            p.remove(child)
    props = _ppr(p)
    _prop(props, "spacing", before="0", after="0", line="480", lineRule="auto")
    indent = _prop(props, "ind", left="720", hanging="720")
    for attr in ("firstLine", "firstLineChars", "leftChars", "start", "startChars", "hangingChars"):
        indent.attrib.pop(W + attr, None)
    _prop(props, "jc", val="left")
    for role, text in parts:
        target = p
        if role == "link" and re.fullmatch(r"https?://[^\s<>]+", text, re.I):
            if text not in targets:
                rel_id = f"rIdAPA{len(rels) + 1}"
                while any(element.get("Id") == rel_id for element in rels):
                    rel_id += "x"
                etree.SubElement(rels, PKG + "Relationship", Id=rel_id, Type=REL + "hyperlink",
                                 Target=text, TargetMode="External")
                targets[text] = rel_id
            target = etree.SubElement(p, W + "hyperlink", {"{" + REL.rstrip("/") + "}id": targets[text]})
        run = etree.SubElement(target, W + "r")
        formatting = copy.deepcopy(base) if base is not None else etree.Element(W + "rPr")
        run.append(formatting)
        italic = "1" if role == "italic" else "0"
        _prop(formatting, "i", val=italic)
        _prop(formatting, "iCs", val=italic)
        etree.SubElement(run, W + "t", {SPACE: "preserve"}).text = text


def style_apa_docx(path, records):
    """Atomically style exactly one matching reference section; report real entry counts."""
    from lxml import etree
    from editable_manuscript import REF_HEADING

    entries = apa_entries(records)
    if not entries:
        return {"styled_entries": 0, "hyperlinks": 0}
    expected = ["".join(t for _, t in parts) for _, parts in entries]
    path = Path(path)
    with tempfile.TemporaryDirectory(dir=path.parent) as temp:
        candidate = Path(temp) / "styled.docx"
        with zipfile.ZipFile(path) as src:
            root = etree.fromstring(src.read("word/document.xml"), etree.XMLParser(resolve_entities=False))
            body = root.find(W + "body")
            paragraphs = [p for p in body.findall(W + "p") if _text(p).strip()]
            sections = [i for i, p in enumerate(paragraphs) if REF_HEADING.fullmatch(_text(p).strip())
                        and [_text(entry) for entry in paragraphs[i + 1:i + 1 + len(entries)]] == expected]
            if len(sections) != 1:
                raise ValueError("edited text and Word bibliography do not identify one matching section")
            heading = sections[0]
            rels = (etree.fromstring(src.read(RELS)) if RELS in src.namelist()
                    else etree.Element(PKG + "Relationships", nsmap={None: PKG[1:-1]}))
            targets = {rel.get("Target"): rel.get("Id") for rel in rels if rel.get("Type") == REL + "hyperlink"
                       and rel.get("TargetMode") == "External"}
            header = paragraphs[heading]
            _prop(_ppr(header), "jc", val="center")
            for run in header.iter(W + "r"):
                rpr = run.find(W + "rPr")
                if rpr is None:
                    rpr = etree.Element(W + "rPr")
                    run.insert(0, rpr)
                _prop(rpr, "b", val="1")
            for i, (_, parts) in enumerate(entries, heading + 1):
                _format_entry(paragraphs[i], parts, rels, targets)
                if _text(paragraphs[i]) != expected[i - heading - 1]:
                    raise ValueError("Word typography edit changed the reference text")
            changes = {"word/document.xml": root, RELS: rels}
            with zipfile.ZipFile(candidate, "w", zipfile.ZIP_DEFLATED) as dest:
                for member in src.infolist():
                    if member.filename not in changes:
                        dest.writestr(member, src.read(member))
                for name, xml in changes.items():
                    dest.writestr(name, etree.tostring(xml, encoding="utf-8", xml_declaration=True))
        candidate.replace(path)
    return {"styled_entries": len(entries), "hyperlinks": sum(
        1 for _, parts in entries for role, text in parts if role == "link" and re.fullmatch(r"https?://[^\s<>]+", text, re.I))}
