"""Select the requested papers and retain original locations for structured source material."""
from __future__ import annotations

import json
import csv
import re
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

SUPPORTED = {".pdf", ".docx", ".md", ".txt", ".json"}
METADATA = {"case.json", "task.md", "resources.yaml", "config.json", "challenge.json", "readme.md",
            "corpus-manifest.json", "result.json"}
SUPPORT = re.compile(r"(?:^|[\s/_.-])(?:glossary|terminology|references?|instructions?|rubric|style[-_ ]?guide|metadata|schema|词表|术语表|参考文献)(?:$|[\s/_.-])", re.I)
PAPER_KEYS = ("paragraphs", "sections", "passages", "source_text", "original_text", "full_text", "text", "content",
              "body", "paper", "document", "article")
TARGET_KEYS = ("target_file", "target_files", "source_file", "source_files", "pdf_path", "document_path", "source_path", "manuscript_path", "paper_path")


def read_case(input_path, case_path=None):
    root = input_path if input_path.is_dir() else input_path.parent
    path = Path(case_path) if case_path else root / "case.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8-sig"))
    if case_path:
        raise FileNotFoundError(f"Missing case: {path}")
    return {}


def json_paragraphs(value, pointer="#"):
    """Convert a material object into text units with JSON pointers or original paragraph IDs."""
    if isinstance(value, str):
        if value.strip():
            yield value, {"json_pointer": pointer}
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from json_paragraphs(item, f"{pointer}/{i}")
    elif isinstance(value, dict):
        if isinstance(value.get("title"), str) and any(value.get(k) for k in PAPER_KEYS):
            yield "# " + value["title"], {"json_pointer": pointer + "/title"}
        if isinstance(value.get("abstract"), str):
            yield value["abstract"], {"json_pointer": pointer + "/abstract"}
        for key in PAPER_KEYS:
            part = value.get(key)
            if not isinstance(part, (str, list, dict)) or not part:
                continue
            for text, anchor in json_paragraphs(part, f"{pointer}/{key}"):
                pid = value.get("paragraph_id") or value.get("para_id") or value.get("id")
                if pid is not None:
                    anchor.setdefault("paragraph_id", str(pid))
                yield text, anchor
            # Alternative text serializations refer to the same passage.
            return


def _role(item):
    return str(item.get("role") or item.get("purpose") or item.get("type") or "").lower()


def _files(value):
    if isinstance(value, list):
        for item in value:
            yield from _files(item)
    elif isinstance(value, str):
        yield value, ""
    elif isinstance(value, dict):
        name = next((value.get(k) for k in ("path", "file", "filename", "file_path", "source_file", "uri") if isinstance(value.get(k), str)), None)
        if name:
            yield name, _role(value)


def select_material(input_path, case, out_dir, excluded_dirs=()):
    root = (input_path if input_path.is_dir() else input_path.parent).resolve()
    warnings = []
    auxiliary = []
    selected = []
    seen = set()

    def add_path(name, role="", explicit=False):
        portable = name.replace("\\", "/")
        path = (root / (portable[7:] if portable.startswith("/input/") else portable)).resolve()
        if not path.is_relative_to(root):
            warnings.append({"code": "source_outside_input", "source": name})
            return
        label = path.relative_to(root).as_posix()
        if SUPPORT.search(role) or role in {"supporting", "support", "context", "reference_material", "supplement", "auxiliary"} or (not explicit and SUPPORT.search(label)):
            auxiliary.append(label)
            return
        if path in seen:
            return
        if not path.is_file() or path.suffix.lower() not in SUPPORTED:
            warnings.append({"code": "source_missing_or_unsupported", "source": label})
            return
        if not explicit and path.suffix.lower() == ".json":
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
                if not next(json_paragraphs(payload), None):
                    return
            except (ValueError, UnicodeError):
                return
        selected.append((label, path, None))
        seen.add(path)

    if input_path.is_file() and input_path.name.lower() != "case.json":
        add_path(input_path.resolve().as_posix(), explicit=True)
        return selected, auxiliary, warnings

    target = [x for key in TARGET_KEYS for x in _files(case.get(key))]
    inputs = [x for key in ("input_files", "files", "materials", "inputs") for x in _files(case.get(key))]
    if not target:
        target = [(p, role) for p, role in inputs if role in {"primary", "target", "manuscript", "translation_target", "source_paper"}]
    if target:
        for name, role in target:
            add_path(name, role, explicit=True)
        auxiliary += [p for p, _ in inputs if p not in {name for name, _ in target}]
    else:
        for key in ("source_text", "original_text", "passages", "paragraphs", "manuscript_text", "full_text",
                    "source_document", "manuscript", "document", "materials", "input"):
            value = case.get(key)
            if key in {"materials", "input"} and isinstance(value, str):
                continue
            if key == "materials" and inputs:
                continue
            if key in {"manuscript", "document", "source_document"} and isinstance(value, str) and "\n" not in value:
                if Path(value).suffix.lower() in SUPPORTED and len(value) < 512:
                    add_path(value, explicit=True)
                    return selected, auxiliary, warnings
            if isinstance(value, (str, list, dict)) and next(json_paragraphs(value), None):
                selected.append((f"case.json#/{key}", None, list(json_paragraphs(value, f"#/{key}"))))
                auxiliary += [p for p, _ in inputs]
                break
        if not selected:
            if inputs:
                for name, role in inputs:
                    add_path(name, role)
            elif root.is_dir():
                for f in sorted(root.rglob("*")):
                    if (not f.is_file() or f.is_symlink() or f.name.lower() in METADATA
                            or f.suffix.lower() not in SUPPORTED or any(f.resolve().is_relative_to(x.resolve())
                            for x in (out_dir, *excluded_dirs))):
                        continue
                    add_path(f.relative_to(root).as_posix())
                if len(selected) > 1:
                    warnings.append({"code": "translation_scope_unconfirmed", "sources": [s[0] for s in selected],
                                     "detail": "Multiple papers discovered without a target declaration. Confirm against task.md."})
    return selected, auxiliary, warnings


def read_glossary_files(root, case, auxiliary):
    """Load designated term maps without treating supporting documents as papers."""
    requested = [name for key in ("glossary_file", "glossary_path", "terminology_file") for name, _ in _files(case.get(key))]
    requested.extend(name for name in auxiliary if re.search(r"glossary|terminology|术语|词表", name, re.I))
    for key in ("input_files", "materials", "files"):
        requested.extend(p for p, role in _files(case.get(key)) if re.search(r"glossary|terminology|术语|词表", role, re.I))
    root = root.resolve()
    terms = {}
    issues = []
    for name in dict.fromkeys(requested):
        relative = name.replace("\\", "/").removeprefix("/input/")
        path = (root / relative).resolve()
        try:
            if not path.is_relative_to(root):
                raise ValueError("Glossary path outside input")
            if path.suffix.lower() == ".json":
                rows = json.loads(path.read_text(encoding="utf-8-sig"))
                if isinstance(rows, dict):
                    rows = rows.get("entries", rows.get("terms", rows))
                if isinstance(rows, dict):
                    rows = rows.items()
                elif isinstance(rows, list):
                    rows = [(x.get("source_term") or x.get("term_en") or x.get("source"),
                             x.get("target_zh") or x.get("term_zh") or x.get("target"))
                            for x in rows if isinstance(x, dict)]
                else:
                    raise ValueError("Unsupported JSON glossary")
            elif path.suffix.lower() in {".csv", ".tsv"}:
                with path.open(encoding="utf-8-sig", newline="") as stream:
                    rows = [(r[0], r[1]) for r in csv.reader(stream, delimiter="\t" if path.suffix.lower() == ".tsv" else ",")
                            if len(r) >= 2 and r[0].lower().strip() not in {"source", "source_term", "term_en", "english", "英文"}]
            else:
                raise ValueError("Supply JSON, CSV or TSV terminology, or review the auxiliary file manually.")
            cleaned = {a.strip(): b.strip() for a, b in rows if isinstance(a, str) and a.strip() and isinstance(b, str) and b.strip()}
            if not cleaned:
                raise ValueError("No term pairs in glossary")
            for term, translation in cleaned.items():
                if term in terms and terms[term] != translation:
                    issues.append({"code": "glossary_conflict", "term": term, "source": name})
                else:
                    terms[term] = translation
        except (OSError, ValueError, csv.Error) as exc:
            issues.append({"code": "unreadable_glossary", "source": name, "detail": str(exc)})
    return terms, issues


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"


def docx_units(path):
    """Walk OOXML body order without assuming a rendered page number."""
    with ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    body = root.find(W + "body")
    if body is None:
        raise ValueError("DOCX has no document body")

    def text_of(elem):
        text = []
        for node in elem.iter():
            if node.tag in {W + "t", M + "t"}:
                text.append(node.text or "")
            elif node.tag in {W + "tab", W + "br"}:
                text.append(" ")
        return "".join(text).strip()

    p_count = t_count = 0
    for child in body:
        if child.tag == W + "p":
            p_count += 1
            txt = text_of(child)
            if not txt:
                continue
            pstyle = child.find(f"{W}pPr/{W}pStyle")
            style = pstyle.get(W + "val", "") if pstyle is not None else ""
            if style.lower() == "title":
                txt = "# " + txt
            elif m := re.fullmatch(r"Heading\s*([1-4])", style, re.I):
                txt = "#" * min(4, int(m[1]) + 1) + " " + txt
            yield txt, {"docx_paragraph": p_count}
        elif child.tag == W + "tbl":
            t_count += 1
            rows = [[text_of(cell).replace("|", "｜") for cell in row.findall(W + "tc")] for row in child.findall(W + "tr")]
            if rows and rows[0]:
                lines = ["| " + " | ".join(row) + " |" for row in rows]
                lines.insert(1, "| " + " | ".join("---" for _ in rows[0]) + " |")
                yield "\n".join(lines), {"docx_table": t_count}

