"""Discover case inputs and read manuscripts, bibliographies and supporting evidence in place."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from parse_bib_sources import make_raw_library, parse_json_records, parse_text_source, read_source_text


DOCUMENTS = {".md", ".markdown", ".txt", ".tex", ".docx", ".pdf"}
EXPORTS = {".bib", ".ris", ".enw"}
TABLES = {".csv", ".tsv"}
IGNORED = {"task.md", "challenge.json", "config.json", "case.json", "readme.md", "resources.yaml"}
MANUSCRIPT = re.compile(r"manuscript|(?:^|[_ .-])(?:ms|draft|paper|article|main|body)(?:$|[_ .-])|稿件|原稿|文稿|正文|手稿|初稿", re.I)
LIBRARY = re.compile(r"bibliograph|references?|(?:^|[_ .-])(?:refs?|bib|library|citations?|endnote|cnki)(?:$|[_ .-])|文献库|参考文献|引文库|题录", re.I)
SUPPORT = re.compile(r"supplement|evidence|sources?|(?:^|[_ .-])papers(?:$|[_ .-])|全文|原文证据|补充材料|来源", re.I)
INSTRUCTION = re.compile(r"(?:^|[_ .-])(?:instructions?|rubric|readme|style|requirements?|guidelines?|task)(?:$|[_ .-])|评分|任务说明|格式要求", re.I)
REF_HEADING = re.compile(r"(?im)^[ \t]*(?:#{1,6}\s*)?(?:\d+(?:\.\d+)*[.、]?\s*)?(?:References|Bibliography|参考文献|引用文献)\s*[:：]?[ \t]*$")
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MS_KEYS = {"manuscript", "manuscript_text", "manuscript_file", "draft", "draft_text", "draft_file", "article_text", "原稿", "稿件", "正文"}
BIB_KEYS = {"bibliography", "bibliography_text", "bibliography_file", "bib_files", "library", "reference_library", "bibliography_files", "references", "reference_files", "bibtex", "文献库", "参考文献"}
SKIP_KEYS = {"output_contract", "output_schema", "outputs", "expected_outputs", "rubric", "examples", "insert_claims", "scoring"}


def read_docx_paragraphs(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as document:
        root = ET.fromstring(document.read("word/document.xml"))
    lines = []
    for paragraph in root.iter(W + "p"):
        pieces = []
        for node in paragraph.iter():
            if node.tag == W + "t":
                pieces.append(node.text or "")
            elif node.tag == W + "tab":
                pieces.append("\t")
            elif node.tag in (W + "br", W + "cr"):
                pieces.append("\n")
        lines.append("".join(pieces))
    return lines


def _pdf_pages(path: Path) -> list[str]:
    try:
        import fitz
    except ImportError:
        fitz = None
    if fitz is not None:
        with fitz.open(path) as document:
            if document.needs_pass:
                raise ValueError("encrypted_pdf")
            return [page.get_text("text", sort=True) for page in document]
    command = shutil.which("pdftotext")
    if command:
        run = subprocess.run([command, "-layout", "-enc", "UTF-8", str(path), "-"],
                             capture_output=True, timeout=60, check=True)
        return run.stdout.decode("utf-8").rstrip("\f").split("\f")
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ValueError("PDF extraction requires PyMuPDF, pdftotext or pypdf") from exc
    return [page.extract_text() or "" for page in PdfReader(path).pages]


def _document_text(path: Path) -> tuple[str, list[dict]]:
    if path.suffix.lower() == ".docx":
        paragraphs = read_docx_paragraphs(path)
        return "\n".join(paragraphs), [{"paragraph": n, "text": p} for n, p in enumerate(paragraphs, 1) if p.strip()]
    if path.suffix.lower() == ".pdf":
        pages = _pdf_pages(path)
        return "\n".join(pages), [{"page": n, "text": page} for n, page in enumerate(pages, 1)]
    text = read_source_text(path)
    return text, [{"line": n, "text": line} for n, line in enumerate(text.splitlines(), 1) if line.strip()]


def _pointers(obj, pointer=""):
    if isinstance(obj, dict):
        role = str(obj.get("role") or obj.get("kind") or "").lower()
        if _file_descriptor(obj) and (LIBRARY.search(role) or MANUSCRIPT.search(role)):
            yield ("bibliography" if LIBRARY.search(role) else "manuscript"), pointer, obj
        for key, value in obj.items():
            name = str(key).lower()
            if name in SKIP_KEYS:
                continue
            sub = pointer + "/" + str(key).replace("~", "~0").replace("/", "~1")
            if name in MS_KEYS or name in BIB_KEYS:
                yield ("manuscript" if name in MS_KEYS else "bibliography"), sub, value
            else:
                yield from _pointers(value, sub)
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            yield from _pointers(item, pointer + f"/{idx}")


def _manuscript_text(value):
    if isinstance(value, str):
        return value.replace("\r\n", "\n").replace("\r", "\n")
    if isinstance(value, list):
        return "\n".join(filter(None, (_manuscript_text(v) for v in value)))
    if isinstance(value, dict):
        for key in ("text", "content", "body", "paragraphs", "sections", "manuscript_text", "manuscript", "draft", "正文"):
            if key in value:
                body = _manuscript_text(value[key])
                heading = value.get("heading") or value.get("title")
                return heading + "\n" + body if isinstance(heading, str) and heading and body else body
    return ""


def _resolve_file(value, root, files):
    if not isinstance(value, str) or not value.strip() or any(c in value for c in "\n\r\0") or len(value) > 512:
        return None
    name = value.strip().replace("\\", "/")
    if name.startswith("/input/"):
        name = name[len("/input/"):]
    if ".." in Path(name).parts or re.match(r"^[A-Za-z]:", name) and not Path(name).is_absolute():
        return None
    unresolved = root / name
    if not unresolved.is_relative_to(root):
        return None
    candidate = root
    for part in unresolved.relative_to(root).parts:
        candidate /= part
        if candidate.is_symlink():
            return None
    direct = unresolved.resolve()
    if direct in files:
        return direct
    if "/" in name:
        return None
    by_name = [p for p in files if p.name.casefold() == Path(name).name.casefold()]
    return by_name[0] if len(by_name) == 1 else None


def _looks_like_filename(value):
    return (
        isinstance(value, str) and "\n" not in value and len(value) < 260
        and Path(value).suffix.lower() in DOCUMENTS | EXPORTS | TABLES | {".json"}
    )


def _file_descriptor(obj):
    if isinstance(obj, dict):
        return next((obj.get(k) for k in ("path", "file", "file_path", "filename") if isinstance(obj.get(k), str)), None)
    return obj


def _candidate_score(path: Path, root: Path) -> int:
    parts = path.relative_to(root)
    score = 8 if MANUSCRIPT.search(path.stem) else 0
    if any(MANUSCRIPT.search(p) for p in parts.parts[:-1]):
        score += 4
    if SUPPORT.search(path.stem) or any(SUPPORT.search(p) for p in parts.parts[:-1]):
        score -= 12
    if path.suffix.lower() == ".pdf":
        score -= 1
    return score


def _input_files(root: Path) -> list[Path]:
    files = []
    for base, folders, names in os.walk(root, followlinks=False):
        directory = Path(base)
        folders[:] = [
            name for name in sorted(folders)
            if not name.startswith(".") and not (directory / name).is_symlink()
            and (directory / name).resolve() == directory / name
        ]
        for name in sorted(names):
            path = directory / name
            if not name.startswith(".") and not path.is_symlink() and path.resolve() == path and path.is_file():
                files.append(path)
    return files


def ingest_materials(input_dir: Path, case_data: dict, *, case_path: Path | None = None) -> dict:
    root = Path(input_dir).resolve()
    case_file = Path(case_path).resolve() if case_path else root / "case.json"
    case_source = case_file.relative_to(root).as_posix() if case_file.is_relative_to(root) else case_file.name
    files = _input_files(root)
    file_set = set(files)
    material_files = [p for p in files if p != case_file and p.name.lower() not in IGNORED]
    ms_path, ms_text, ms_source = None, "", None
    selection = {"basis": "none", "source": None}
    bib_paths, inline_records, inline_sources = [], [], []
    uncertainties, errors, inventory, passages = [], [], [], []
    source_name = lambda path: path.relative_to(root).as_posix()
    missing_declarations = set()
    doc_cache = {}

    def read_document(path):
        if path not in doc_cache:
            doc_cache[path] = _document_text(path)
        return doc_cache[path]

    def note_error(source, exc):
        errors.append({"file": source, "error": str(exc)})
        uncertainties.append({"code": "material_parse_error", "source": source, "detail": str(exc)})

    def declared(role, pointer, value, source):
        nonlocal ms_path, ms_text, ms_source
        descriptor = _file_descriptor(value)
        path = _resolve_file(descriptor, root, file_set)
        location = source + "#" + pointer
        if path is not None:
            if role == "manuscript":
                if ms_source is not None and ms_source != source_name(path):
                    if selection.get("declaration") == case_source + "#/manuscript_file":
                        selection.setdefault("other_candidates", []).append(source_name(path))
                    else:
                        uncertainties.append({"code": "multiple_manuscripts_declared", "selected": ms_source, "other": source_name(path)})
                else:
                    ms_path, ms_source = path, source_name(path)
                    selection.update(basis="declared_file", source=ms_source, declaration=location)
            elif path not in bib_paths:
                bib_paths.append(path)
            return
        if _looks_like_filename(descriptor):
            missing_declarations.add((role, location, descriptor))
            if not isinstance(value, dict) or not _manuscript_text(value):
                return
        if role == "manuscript":
            text = _manuscript_text(value)
            if text.strip() and ms_source is None:
                ms_text, ms_source = text, location
                selection.update(basis="inline_manuscript", source=location)
        elif isinstance(value, list) and any(_looks_like_filename(_file_descriptor(v)) for v in value):
            for i, v in enumerate(value):
                declared("bibliography", f"{pointer}/{i}", v, source)
        else:
            if isinstance(value, dict) and "text" in value and "title" not in value:
                value = value["text"]
            try:
                recs = parse_text_source(value, location) if isinstance(value, str) else parse_json_records(value, source, pointer)
                if recs:
                    inline_sources.append(location)
                    inline_records.extend(recs)
                else:
                    uncertainties.append({"code": "unparsed_bibliography", "source": location})
            except (ValueError, TypeError) as exc:
                note_error(location, exc)

    pointers = list(_pointers(case_data))
    for role, ptr, value in sorted(pointers, key=lambda item: item[1] != "/manuscript_file"):
        declared(role, ptr, value, case_source)

    for p in material_files:
        if p in bib_paths or p == ms_path:
            continue
        ext = p.suffix.lower()
        if ext in EXPORTS or (
            ext in DOCUMENTS | TABLES and LIBRARY.search(p.stem) and not MANUSCRIPT.search(p.stem)
        ):
            bib_paths.append(p)
        elif ext == ".json":
            try:
                obj = json.loads(read_source_text(p))
                if ms_source is None and MANUSCRIPT.search(p.stem) and _manuscript_text(obj):
                    ms_path, ms_source = p, source_name(p)
                    selection.update(basis="structured_manuscript", source=ms_source)
                    continue
                looks_like_export = isinstance(obj, list) or isinstance(obj, dict) and (
                    any(k in obj for k in ("records", "items", "entries", "title", "message"))
                    or LIBRARY.search(p.stem)
                )
                if looks_like_export:
                    if parse_json_records(obj, source_name(p)):
                        bib_paths.append(p)
                        continue
                for role, ptr, value in _pointers(obj):
                    declared(role, ptr, value, source_name(p))
            except (OSError, ValueError) as exc:
                note_error(source_name(p), exc)
        elif ext in TABLES:
            try:
                header = read_source_text(p).splitlines()[:1]
                if header and re.search(r"(?:^|[,\t\"])(?:title|题名|doi)(?:[,\t\"]|$)", header[0], re.I):
                    bib_paths.append(p)
            except (ValueError, OSError) as exc:
                note_error(source_name(p), exc)
        elif ext == ".txt":
            try:
                snippet = read_source_text(p)[:400].lstrip()
                if re.match(r"(?:TY\s*-\s*|RT\s+\S|%[0T]\s+|@\w+\s*[\({]|\[\d+\]\s*\S+.{1,160}\[\w+\])", snippet):
                    bib_paths.append(p)
            except (ValueError, OSError) as exc:
                note_error(source_name(p), exc)
    if ms_source is None and not any(role == "manuscript" for role, _, _ in missing_declarations):
        candidates = sorted((p for p in material_files if p not in bib_paths and p.suffix.lower() in DOCUMENTS and not INSTRUCTION.search(p.stem)),
                            key=lambda p: (-_candidate_score(p, root), str(p)))
        if candidates:
            best_score = _candidate_score(candidates[0], root)
            best = [p for p in candidates if _candidate_score(p, root) == best_score]
            if len(best) == 1 and best_score >= -1:
                ms_path, ms_source = best[0], source_name(best[0])
                selection.update(basis="file_role_inference", source=ms_source, candidates=[
                    {"source_file": source_name(p), "score": _candidate_score(p, root)} for p in candidates
                ])
            else:
                uncertainties.append({"code": "ambiguous_manuscript", "candidates": [source_name(p) for p in candidates]})

    if ms_path is not None:
        try:
            if ms_path.suffix.lower() == ".json":
                ms_text = _manuscript_text(json.loads(read_source_text(ms_path)))
            else:
                ms_text, _ = read_document(ms_path)
            if not ms_text.strip():
                raise ValueError("no_readable_manuscript_text")
        except Exception as exc:
            note_error(ms_source, exc)
    for role, locator, value in sorted(missing_declarations):
        uncertainties.append({"code": "declared_file_missing", "role": role, "source": locator, "path": value})
        if role == "bibliography":
            errors.append({"file": value, "declaration": locator, "error": "declared_file_unavailable_in_input"})

    records, sources = list(inline_records), list(inline_sources)
    for p in bib_paths:
        name = source_name(p)
        sources.append(name)
        try:
            text, locations = read_document(p)
            recs = parse_text_source(text, name, p.suffix.lower())
            if not recs:
                raise ValueError("zero_records_parsed")
            if p.suffix.lower() in {".pdf", ".docx"}:
                for rec in recs:
                    origin = rec["source_provenance"][0]
                    raw = origin.get("verbatim", "")
                    needle = raw[:30]
                    for passage in locations:
                        if needle and needle in " ".join(passage["text"].split()):
                            origin.update({k: v for k, v in passage.items() if k != "text"})
                            break
            records.extend(recs)
        except Exception as exc:
            note_error(name, exc)
    # Numbered lists inside the actual manuscript are primary evidence of legacy numbering.
    if ms_text and (match := REF_HEADING.search(ms_text)):
        after = ms_text[match.end():]
        tail = re.search(r"(?im)^[ \t]*(?:#{1,6}\s*)?(?:Appendix\b|附录|Acknowledg(?:e)?ments\b|致谢).*?$", after)
        after = after[:tail.start()] if tail else after
        recs = parse_text_source(after, ms_source)
        offset = ms_text[:match.end()].count("\n")
        for rec in recs:
            origin = rec["source_provenance"][0]
            if "line" in origin:
                origin["line"] += offset
            origin["section"] = "manuscript_bibliography"
        if recs:
            records.extend(recs)
            sources.append(ms_source)

    numbered = {}
    for rec in records:
        origin = rec["source_provenance"][0]
        if "original_number" in origin:
            numbered.setdefault(origin["original_number"], []).append(rec)
    for label, entries in numbered.items():
        primary = [entry for entry in entries if entry["source_provenance"][0].get("section") == "manuscript_bibliography"]
        effective = primary or entries
        titles = {str(entry["title"]).strip().casefold() for entry in effective if entry["title"]}
        dois = {str(entry["doi"]).casefold() for entry in effective if entry["doi"]}
        if len(dois) > 1 or len(titles) > 1 and not (len(dois) == 1 and all(entry["doi"] for entry in effective)):
            uncertainties.append({"code": "conflicting_original_number", "number": label,
                                  "candidates": [entry["source_provenance"][0] for entry in effective]})
    for p in material_files:
        name = source_name(p)
        role = "manuscript" if p == ms_path else "bibliography" if p in bib_paths else "other"
        if role == "other" and p.suffix.lower() in DOCUMENTS and not INSTRUCTION.search(p.stem):
            role = "supporting_document"
            try:
                _, locations = read_document(p)
                if not any(loc["text"].strip() for loc in locations):
                    raise ValueError("no_text_layer; inspect original pages with OCR if required")
                passages.extend({"source_file": name, **loc} for loc in locations if loc["text"].strip())
            except Exception as exc:
                note_error(name, exc)
        inventory.append({"source_file": name, "role": role, "bytes": p.stat().st_size})
    return {"manuscript_path": ms_path, "manuscript_text": ms_text, "manuscript_source": ms_source,
            "is_latex": (ms_path is not None and ms_path.suffix.lower() == ".tex") or "\\begin{document}" in ms_text,
            "raw_library": make_raw_library(records, sources, errors), "uncertainties": uncertainties,
            "material_inventory": inventory, "manuscript_selection": selection, "evidence_passages": passages}
