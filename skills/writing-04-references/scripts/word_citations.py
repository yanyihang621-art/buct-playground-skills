"""Resolve Word, Zotero and EndNote field instructions and synchronize anchored notes."""
from __future__ import annotations

import copy
import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from io_utils import normalize_doi, normalize_title


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MARK = "{urn:reference-skill:transient}part"
PARTS = ("word/document.xml", "word/footnotes.xml", "word/endnotes.xml")
B = "{http://schemas.openxmlformats.org/officeDocument/2006/bibliography}"


def _visible(node):
    return "".join(el.text or "" if el.tag == W + "t" else "\t" if el.tag == W + "tab" else "\n"
                   for el in node.iter() if el.tag in (W + "t", W + "tab", W + "br", W + "cr"))


def _instruction_fields(root):
    """Walk complex fields across runs/paragraphs, retaining the exact result vs instruction boundary."""
    stack = []
    for node in root.iter():
        if node.tag == W + "fldSimple":
            yield {"simple": node, "start": node, "code": node.get(W + "instr", ""),
                   "display": list(node.iter(W + "t")), "control": [], "nested": bool(stack)}
        elif node.tag == W + "fldChar":
            kind = node.get(W + "fldCharType")
            if kind == "begin":
                if stack:
                    stack[-1]["nested"] = True
                stack.append({"start": node, "code": "", "display": [], "control": [node],
                              "show": False, "nested": bool(stack)})
            elif stack and kind == "separate":
                stack[-1]["show"] = True
                stack[-1]["control"].append(node)
            elif stack and kind == "end":
                field = stack.pop()
                field["control"].append(node)
                yield field
        elif stack:
            field = stack[-1]
            if node.tag == W + "instrText" and not field["show"]:
                field["code"] += node.text or ""
                field["control"].append(node)
            if node.tag == W + "t" and field["show"]:
                field["display"].append(node)
    for field in stack:
        field["malformed"] = True
        yield field


def _source_tags(pkg):
    sources = {}
    for name in pkg.namelist():
        if not re.fullmatch(r"customXml/item\d+\.xml", name, re.I):
            continue
        try:
            root = ElementTree.fromstring(pkg.read(name))
        except ElementTree.ParseError:
            continue
        for item in root.iter(B + "Source"):
            tag = item.findtext(B + "Tag")
            if tag:
                sources[tag] = {"key": tag, "title": item.findtext(B + "Title"), "doi": item.findtext(B + "DOI")}
    return sources


def embedded_word_records(source, label):
    """Extract actual source data stored inside Word when no separate library is available."""
    try:
        return _embedded_word_records(source, label)
    except (ImportError, OSError, KeyError, ValueError, SyntaxError, zipfile.BadZipFile):
        return []  # The input inventory separately records an unreadable or unsupported package.


def _embedded_word_records(source, label):
    from parse_bib_sources import parse_json_records
    try:
        from lxml import etree
    except ImportError:
        return []
    records = {}

    def append(data, part, index, **location):
        if not data.get("title"):
            return
        key = data.get("cite_key") or data.get("citation-key")
        # Keep identifiers repeatable within the input even if local Zotero IDs are absent.
        record = {**data, "cite_key": key or f"embedded_{len(records) + 1}"}
        identity = json.dumps(data, sort_keys=True, ensure_ascii=False)
        if identity not in records:
            parsed = parse_json_records(record, label)
            if not parsed:
                return
            records[identity] = parsed[0]
            records[identity]["source_provenance"] = []
        records[identity]["source_provenance"].append(
            {"source_file": str(label), "raw_key": records[identity]["raw_key"],
             "format": "docx_embedded_source", "part": part, "entry": index, **location})

    with zipfile.ZipFile(source) as pkg:
        for part in pkg.namelist():
            if not re.fullmatch(r"customXml/item\d+\.xml", part, re.I):
                continue
            try:
                root = ElementTree.fromstring(pkg.read(part))
            except ElementTree.ParseError:
                continue
            for idx, s in enumerate(root.iter(B + "Source"), 1):
                people = [", ".join(filter(None, (p.findtext(B + "Last"), p.findtext(B + "First"))))
                          for p in s.findall(".//" + B + "Author/" + B + "NameList/" + B + "Person")]
                corp = s.findtext(".//" + B + "Author/" + B + "Corporate")
                authors = people or ([corp] if corp else [])
                append({"cite_key": s.findtext(B + "Tag"), "title": s.findtext(B + "Title"), "authors": authors,
                        "year": s.findtext(B + "Year"), "journal": s.findtext(B + "JournalName") or s.findtext(B + "Publisher"),
                        "doi": s.findtext(B + "DOI"), "volume": s.findtext(B + "Volume"), "issue": s.findtext(B + "Issue"),
                        "pages": s.findtext(B + "Pages"), "url": s.findtext(B + "URL")}, part, idx)
        for part in PARTS:
            if part not in pkg.namelist():
                continue
            root = etree.fromstring(pkg.read(part), etree.XMLParser(resolve_entities=False))
            pnums = {p: i for i, p in enumerate(root.iter(W + "p"), 1)}
            for idx, field in enumerate(_instruction_fields(root), 1):
                code = field["code"].strip()
                loc = {"paragraph": pnums.get(next(field["start"].iterancestors(W + "p"), None))}
                try:
                    if re.match(r"ADDIN\s+ZOTERO_ITEM\b", code, re.I):
                        items = json.loads(code[code.index("{"):]).get("citationItems", [])
                        for j, item in enumerate(items, 1):
                            rec = item.get("itemData") or {}
                            if isinstance(rec, dict):
                                append({**rec, "cite_key": item.get("citationKey") or rec.get("citation-key")},
                                       part, idx, item=j, **loc)
                    elif re.match(r"ADDIN\s+EN\.CITE\s+<", code, re.I):
                        for j, rec in enumerate(ElementTree.fromstring(code[code.index("<"):]).iter("record"), 1):
                            append({"title": rec.findtext(".//titles/title"), "cite_key": rec.findtext("label"),
                                    "authors": [p.text for p in rec.findall(".//authors/author") if p.text],
                                    "year": rec.findtext(".//dates/year"), "venue": rec.findtext(".//periodical/full-title")
                                    or rec.findtext(".//titles/secondary-title"), "doi": rec.findtext(".//electronic-resource-num"),
                                    "volume": rec.findtext("volume"), "issue": rec.findtext("number"),
                                    "pages": rec.findtext("pages")}, part, idx, item=j, **loc)
                except (ValueError, TypeError, AttributeError, ElementTree.ParseError):
                    continue  # prepare_word reports unrecognized fields alongside their original locations.
    return list(records.values())


def _field_items(code, tags):
    code = code.strip()
    if re.match(r"(?:BIBLIOGRAPHY\b|ADDIN\s+(?:ZOTERO_BIBL|EN\.REFLIST))", code, re.I):
        return "bibliography", []
    if re.match(r"CITATION\s", code, re.I):
        if re.search(r"\\(?![ml]\b)\w+", code, re.I):
            raise ValueError("Word CITATION has page, display or style switches")
        found = re.findall(r'(?:^CITATION|\\m)\s+(?:"([^"]+)"|([^\s\\]+))', code, re.I)
        return "Word", [tags.get(a or b, {"key": a or b}) for a, b in found]
    if re.match(r"ADDIN\s+ZOTERO_ITEM\b", code, re.I):
        data = json.loads(code[code.index("{"):])
        rows = []
        for item in data.get("citationItems", []):
            if any(item.get(x) for x in ("locator", "prefix", "suffix", "suppress-author", "author-only")):
                raise ValueError("Zotero locator or citation modifier requires verification")
            rec = item.get("itemData") or {}
            # Numeric Zotero database IDs are local to a library.
            key = item.get("citationKey") or rec.get("citation-key")
            rows.append({"key": key, "title": rec.get("title"), "doi": rec.get("DOI")})
        return "Zotero", rows
    if re.match(r"ADDIN\s+EN\.CITE\b", code, re.I):
        xml = ElementTree.fromstring(code[code.index("<"):])
        result = []
        for cite in xml.iter("Cite"):
            if any(cite.findtext(x) for x in ("Pages", "Prefix", "Suffix", "ExcludeAuth", "ExcludeYear")):
                raise ValueError("EndNote locator or citation modifier requires verification")
            result.append({"title": cite.findtext(".//titles/title"), "doi": cite.findtext(".//electronic-resource-num"),
                           "key": cite.findtext(".//label")})
        return "EndNote", result
    return None, []


def _match(item, records):
    matches = []
    for field, normalizer in (("doi", lambda s: (normalize_doi(s) or "").casefold()),
                              ("title", normalize_title), ("key", str)):
        value = item.get(field)
        if not value:
            continue
        if field == "key":
            candidates = {r["cite_key"] for r in records if str(value) in [r["cite_key"], *r.get("aliases", [])]}
        else:
            candidates = {r["cite_key"] for r in records if r.get(field) and
                          normalizer(str(value)) == normalizer(str(r[field]))}
        if candidates:
            matches.append(candidates)
    if not matches:
        raise ValueError("no field identity matched the supplied bibliography")
    common = set.intersection(*matches)
    if len(common) != 1:
        raise ValueError("field identifiers are ambiguous or contradictory")
    key = common.pop()
    record = next(r for r in records if r["cite_key"] == key)
    if item.get("doi") and record.get("doi") and normalize_doi(item["doi"]).casefold() != normalize_doi(record["doi"]).casefold():
        raise ValueError("field DOI conflicts with matched record")
    if item.get("title") and record.get("title") and normalize_title(item["title"]) != normalize_title(record["title"]):
        raise ValueError("field title differs from matched record; check variant or conflicting metadata")
    if not re.fullmatch(r"[\w.:-]+", key):
        raise ValueError("canonical citation key cannot be encoded losslessly")
    return key


def _freeze(field, value):
    from lxml import etree
    if field.get("malformed") or field["nested"]:
        raise ValueError("unbalanced or nested citation field")
    simple = field.get("simple")
    if value is not None:
        if field["display"]:
            first, *rest = field["display"]
            first.text = value
            for node in rest:
                node.text = ""
        elif simple is not None:
            etree.SubElement(etree.SubElement(simple, W + "r"), W + "t").text = value
        else:
            end = field["control"][-1]
            end.addprevious(etree.Element(W + "t"))
            end.getprevious().text = value
    if simple is not None:
        parent = simple.getparent()
        for child in list(simple):
            simple.addprevious(child)
        parent.remove(simple)
    for node in field["control"]:
        node.getparent().remove(node)


def _bibliography_heading(field):
    from lxml import etree
    from editable_manuscript import REF_HEADING
    para = next(field["start"].iterancestors(W + "p"), None)
    if para is None or para.getparent().tag != W + "body":
        raise ValueError("managed bibliography is nested in a protected editor container")
    previous = para.getprevious()
    while previous is not None and previous.tag == W + "p" and not _visible(previous).strip():
        previous = previous.getprevious()
    if previous is not None and REF_HEADING.fullmatch(_visible(previous).strip()):
        return
    if REF_HEADING.fullmatch(_visible(para).strip()):
        return
    cache = "".join(n.text or "" for n in field["display"])
    if _visible(para).strip() and not cache.lstrip().startswith(_visible(para).strip()):
        raise ValueError("bibliography field shares a paragraph with other text")
    header = etree.Element(W + "p")
    etree.SubElement(etree.SubElement(header, W + "r"), W + "t").text = "References"
    para.addprevious(header)


def _write_package(original, dest, roots):
    from lxml import etree
    with zipfile.ZipFile(original) as src, zipfile.ZipFile(dest, "w") as out:
        for info in src.infolist():
            content = (etree.tostring(roots[info.filename], encoding="UTF-8", xml_declaration=True)
                       if info.filename in roots else src.read(info))
            out.writestr(info, content)


def _expand_notes(roots, audit):
    from lxml import etree
    document = roots[PARTS[0]]
    notes = {}
    for part in PARTS[1:]:
        if part not in roots:
            continue
        for note in roots[part]:
            if note.get(W + "type") in ("separator", "continuationSeparator", "continuationNotice"):
                continue
            notes[(etree.QName(note).localname, note.get(W + "id"))] = note
    seen = set()
    for idx, ref in enumerate(list(document.xpath(".//w:footnoteReference | .//w:endnoteReference", namespaces={"w": W[1:-1]}))):
        kind = etree.QName(ref).localname.removesuffix("Reference")
        key = kind, ref.get(W + "id")
        if key not in notes or key in seen:
            raise ValueError("broken or multiply referenced note anchor: " + str(key))
        seen.add(key)
        run = ref.getparent()
        p = run.getparent()
        if run.tag != W + "r" or p.tag != W + "p":
            raise ValueError("note anchor nested in hyperlinks, fields or tracked revisions")
        after = list(run)[run.index(ref) + 1:]
        tail = list(p)[p.index(run) + 1:]
        # Stage a continuation to place note citations before later citations in this very paragraph.
        resume = etree.Element(W + "p", attrib={MARK: "resume"})
        if after:
            r2 = etree.SubElement(resume, W + "r")
            rpr = run.find(W + "rPr")
            if rpr is not None:
                r2.append(copy.deepcopy(rpr))
            r2.extend(after)
        resume.extend(tail)
        insertion = p
        for n, source_p in enumerate(notes[key].iter(W + "p"), 1):
            value = _visible(source_p)
            if not value.strip():
                continue
            label = f"⟦{kind} {key[1]} ¶{n}⟧ "
            slot = f"{kind}:{key[1]}:{n}"
            shadow = etree.Element(W + "p", attrib={MARK: slot})
            etree.SubElement(etree.SubElement(shadow, W + "r"), W + "t").text = label + value
            insertion.addnext(shadow)
            insertion = shadow
            audit["notes"].append({"part": f"word/{kind}s.xml", "note_id": key[1], "paragraph": n,
                                   "slot": slot, "label": label, "source_text": value})
        if len(resume):
            insertion.addnext(resume)
    if notes.keys() - seen:
        raise ValueError("unanchored note paragraphs exist in the source")


def prepare_word(source, canonical, work):
    """Prepare a private shadow document. The original Word package remains the provenance source."""
    from ingest_materials import read_docx_paragraphs
    ctx = {"source": Path(source), "shadow": None, "text": "", "issues": [], "preserve_native": False,
           "audit": {"fields": [], "notes": [], "field_handling": "Resolved citation fields become static editable text; the input retains live fields."}}
    try:
        ctx["text"] = "\n".join(read_docx_paragraphs(source))
        from lxml import etree
        with zipfile.ZipFile(source) as pkg:
            roots = {part: etree.fromstring(pkg.read(part), etree.XMLParser(resolve_entities=False)) for part in PARTS if part in pkg.namelist()}
            tags = _source_tags(pkg)
        for part, root in roots.items():
            pnums = {p: i for i, p in enumerate(root.iter(W + "p"), 1)}
            for field in list(_instruction_fields(root)):
                code = field["code"]
                para = next(field["start"].iterancestors(W + "p"), None)
                row = {"part": part, "paragraph": pnums.get(para), "instruction_type": code.strip().split()[:2],
                       "cached_text": "".join(n.text or "" for n in field["display"])}
                try:
                    vendor, items = _field_items(code, tags)
                    if vendor is None:
                        continue
                    row["provider"] = vendor
                    keys = [_match(item, canonical["records"]) for item in items]
                    if not keys and vendor != "bibliography":
                        raise ValueError("citation field contains no resolvable items")
                    if vendor == "bibliography":
                        _bibliography_heading(field)
                    row["cite_keys"] = keys
                    _freeze(field, "[" + "; ".join("@" + k for k in keys) + "]" if keys else None)
                    row["disposition"] = "converted_to_static_for_sync"
                except (ValueError, KeyError, TypeError, ElementTree.ParseError) as exc:
                    row.update(disposition="needs_native_review", diagnostic=str(exc))
                    ctx["issues"].append({"code": "word_field_unresolved", **row})
                ctx["audit"]["fields"].append(row)
        _expand_notes(roots, ctx["audit"])
        if ctx["issues"]:
            ctx["preserve_native"] = True
        elif ctx["audit"]["fields"] or ctx["audit"]["notes"]:
            shadow = Path(work) / "word_source_for_sync.docx"
            shadow.parent.mkdir(exist_ok=True, parents=True)
            _write_package(source, shadow, roots)
            ctx.update(shadow=shadow, text="\n".join(read_docx_paragraphs(shadow)))
    except (ImportError, OSError, ValueError, KeyError, zipfile.BadZipFile, SyntaxError) as exc:
        ctx["issues"].append({"code": "word_native_structure_needs_review", "detail": str(exc)})
        ctx["preserve_native"] = True
    return ctx


def restore_word_notes(projected, output, context):
    from lxml import etree
    from editable_manuscript import _replace_run_text
    with zipfile.ZipFile(projected) as pkg:
        roots = {part: etree.fromstring(pkg.read(part), etree.XMLParser(resolve_entities=False))
                 for part in PARTS if part in pkg.namelist()}
    lookup = {row["slot"]: row for row in context["audit"]["notes"]}
    for p in list(roots[PARTS[0]].iter(W + "p")):
        slot = p.get(MARK)
        if not slot:
            continue
        parent = p.getparent()
        if slot == "resume":
            prev = p.getprevious()
            if prev is None or prev.tag != W + "p":
                raise ValueError("lost anchor for Word paragraph continuation")
            prev.extend(list(p))
        else:
            rec = lookup.pop(slot)
            target_note = next(note for note in roots[rec["part"]] if note.get(W + "id") == rec["note_id"])
            target = list(target_note.iter(W + "p"))[rec["paragraph"] - 1]
            text = _visible(p)
            if not text.startswith(rec["label"]):
                raise ValueError("note anchor label changed during synchronization")
            _replace_run_text(target, text[len(rec["label"]):])
        parent.remove(p)
    if lookup:
        raise ValueError("one or more notes could not be restored")
    _write_package(projected, output, roots)
