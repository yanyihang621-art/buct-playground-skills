"""Locate manuscript text and keep editable sources separate from reference material."""

from __future__ import annotations

import csv
import io
import json
import re
import shutil
import subprocess
from pathlib import Path

from export_docx import read_docx


TEXT = {".txt", ".tex", ".md", ".markdown", ".rst"}
MANUSCRIPT = TEXT | {".pdf", ".docx"}
CONTEXT = {".json", ".csv", ".tsv", ".bib", ".yaml", ".yml"}
ROLE_CONTEXT = re.compile(
    r"referenc|glossar|termbase|terminolog|style|guideline|instruction|constraint|background|"
    r"rubric|source.paper|support|dictionary|term.list|term.table|evidence|fact.sheet|"
    r"参考|文献|指南|术语|词表|风格|约束|证据|底稿事实", re.I)
ROLE_DRAFT = re.compile(r"manuscript|draft|chapter|section|abstract|paragraph|passage|待润色|原稿|草稿|章节|摘要", re.I)
IGNORE = {"config.json", "challenge.json", "case.json", "resources.yaml", "readme.md"}
DRAFT_KEYS = {"manuscript", "manuscript_text", "draft", "draft_text", "source_text", "original_text",
              "text_to_polish", "passage", "paragraphs", "sections", "chapters", "original",
              "原稿", "待润色文本", "正文", "原文", "段落", "manuscripts"}
FILE_KEYS = {"manuscript_file", "manuscript_files", "draft_file", "draft_files", "manuscript_path"}
CONTAINERS = {"input", "inputs", "data", "materials", "resources", "input_materials", "documents",
              "payload", "content", "files", "document", "input_data", "source_materials"}
GLOSSARY_KEYS = {"glossary", "custom_glossary", "terminology", "term_map", "termbase", "术语表"}


def _decode(data):
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("gb18030")


def _pdf_pages(path):
    try:
        import fitz
    except ImportError:
        fitz = None
    if fitz is not None:
        with fitz.open(path) as document:
            if document.needs_pass:
                raise ValueError("Encrypted PDF requires a password.")
            return [page.get_text("text", sort=True) for page in document]
    try:
        from pypdf import PdfReader
    except ImportError:
        tool = shutil.which("pdftotext")
        if not tool:
            raise RuntimeError("PDF requires PyMuPDF, pypdf or pdftotext.")
        result = subprocess.run([tool, "-layout", "-enc", "UTF-8", str(path), "-"],
                                check=True, timeout=30, capture_output=True)
        return result.stdout.decode("utf-8").removesuffix("\f").split("\f")
    return [page.extract_text() or "" for page in PdfReader(path).pages]


def _read_rows(path, issues, source, primary):
    suffix = path.suffix.lower()
    try:
        if suffix == ".docx":
            return read_docx(path)
        if suffix == ".pdf":
            rows = []
            for page, contents in enumerate(_pdf_pages(path), 1):
                if not contents.strip():
                    issues.append({"code": "pdf_page_without_text", "source": source, "page": page,
                                   "blocking": primary, "message": "Inspect the page image or use OCR."})
                for line, text in enumerate(contents.splitlines(), 1):
                    rows.append((text, {"location": f"page {page}, line {line}", "page": page}))
            return rows
        text = _decode(path.read_bytes())
        if "\x00" in text:
            raise ValueError("Undecodable or binary input.")
        return [(s, {"location": f"line {n}"}) for n, s in enumerate(text.splitlines(), 1)]
    except Exception as exc:
        issues.append({"code": "unreadable_material", "source": source, "message": str(exc), "blocking": primary})
        return []


def _term_pairs(config):
    if isinstance(config, dict):
        for key in ("entries", "terms", "glossary", "terminology"):
            if isinstance(config.get(key), (dict, list)):
                yield from _term_pairs(config[key])
                return
        for term, pref in config.items():
            if isinstance(pref, str) and pref.strip() and term.strip():
                yield term.strip(), pref.strip()
    if isinstance(config, list):
        for item in config:
            if isinstance(item, (list, tuple)) and len(item) == 2 and all(isinstance(v, str) for v in item):
                yield item[0], item[1]
            if not isinstance(item, dict):
                continue
            target = next((item[k] for k in ("preferred", "canonical", "target", "preferred_term", "规范词", "首选词", "译文", "en")
                           if isinstance(item.get(k), str)), "")
            variants = item.get("variants", [])
            variants = variants if isinstance(variants, list) else [variants]
            for k in ("term", "variant", "source", "source_term", "原词", "原文", "zh"):
                if isinstance(item.get(k), str):
                    variants = [*variants, item[k]]
            for variant in variants:
                if isinstance(variant, str) and target.strip() and variant.strip():
                    yield variant.strip(), target.strip()


def _resolve(root, value):
    """Resolve mounted /input paths and relative paths inside the input tree."""
    if not isinstance(value, str) or not value.strip() or "\n" in value or "\0" in value:
        return None
    name = value.replace("\\", "/")
    name = name.removeprefix("/input/")
    try:
        path = (root / name).resolve()
        if path.is_relative_to(root) and path.is_file():
            return path
    except (OSError, ValueError):
        pass
    return None


def _inline(value, pointer):
    if isinstance(value, str) and value.strip():
        yield pointer, value
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from _inline(item, f"{pointer}/{index}")
    if isinstance(value, dict):
        for key in ("title", "heading", "section_title", "text", "content", "body", "paragraphs",
                    "sections", "sentences", "source_text", "original_text", "manuscript_text"):
            if key in value:
                yield from _inline(value[key], f"{pointer}/{key}")


def _case_items(obj, ptr="case.json#", root=False):
    """Visit input material containers only. Keep requirements and output schemas out of prose."""
    if isinstance(obj, list):
        for n, item in enumerate(obj):
            yield from _case_items(item, f"{ptr}/{n}")
        return
    if not isinstance(obj, dict):
        return
    role = " ".join(str(obj.get(k, "")) for k in ("role", "kind", "type", "name", "label"))
    is_reference = bool(ROLE_CONTEXT.search(role))
    for key, value in obj.items():
        k = str(key).lower()
        ref = ptr + "/" + str(key).replace("~", "~0").replace("/", "~1")
        if k in GLOSSARY_KEYS:
            yield "glossary", ref, value
        elif k in {"output_contract", "outputs", "output_format", "expected_outputs", "deliverables"}:
            continue
        elif k in {"glossary_overrides"} and isinstance(value, list):
            yield "overrides", ref, value
        elif k in FILE_KEYS and not is_reference:
            yield "path", ref, value
        elif k in {"path", "file", "filename"}:
            yield ("reference" if is_reference else "path" if ROLE_DRAFT.search(role) else "asset"), ref, value
        elif k in DRAFT_KEYS or k in {"text", "body", "content"} and (
                root or ROLE_DRAFT.search(role) or isinstance(value, str) and not is_reference):
            if is_reference:
                yield "context", ref, value
            else:
                yield from (("inline", p, v) for p, v in _inline(value, ref))
        elif k in CONTAINERS:
            yield from _case_items(value, ref)
        elif ROLE_CONTEXT.search(k) or k in {"task", "task_description", "register", "audience",
                                              "genre", "language", "requirements", "target_venue"}:
            yield "context", ref, value
        elif k in {"abstract", "introduction", "methods", "results", "discussion", "conclusion"} and not is_reference:
            yield from (("inline", p, v) for p, v in _inline(value, ref))


def load_materials(input_dir: Path, case: dict, sources: list[str] | None = None):
    root = Path(input_dir).resolve()
    files = {p.relative_to(root).as_posix(): p for p in sorted(root.rglob("*"))
             if p.is_file() and p.resolve().is_relative_to(root) and not p.is_symlink()
             and not any(c.startswith(".") for c in p.relative_to(root).parts)
             and p.name.lower() not in IGNORE}
    context, issues, selected, inline, refs = [], [], [], {}, set()
    terms, overrides = {}, []

    def add_terms(value):
        for key, pref in _term_pairs(value):
            if key in terms and terms[key] != pref:
                overrides.append({"term": key, "preferred": pref})
            else:
                terms[key] = pref

    def add_context(label, value):
        body = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        context.append({"source": label, "text": body[:40000], "truncated": len(body) > 40000})

    def add_path(raw, ref):
        p = _resolve(root, raw)
        if p:
            label = p.relative_to(root).as_posix()
            if label not in selected:
                selected.append(label)
            return
        issues.append({"code": "missing_declared_manuscript", "source": ref,
                       "requested": str(raw), "blocking": True})

    for kind, ref, obj in _case_items(case, root=True):
        if kind == "glossary":
            add_terms(obj)
            add_context(ref, obj)
        elif kind == "overrides":
            overrides.extend(v for v in obj if isinstance(v, dict))
        elif kind == "path":
            for value in obj if isinstance(obj, list) else [obj]:
                add_path(value, ref)
        elif kind == "inline":
            p = _resolve(root, obj)
            if p:
                add_path(obj, ref)
            else:
                inline[ref] = obj
        elif kind in {"context", "reference"}:
            add_context(ref, obj)
            for value in obj if isinstance(obj, list) else [obj]:
                value = value.get("path", value.get("file")) if isinstance(value, dict) else value
                p = _resolve(root, value)
                if p:
                    refs.add(p.relative_to(root).as_posix())

    # Structured draft files are common material bundles. Unwrap only recognized
    # prose fields so instructions, IDs and expected outputs cannot become text.
    for label, path in files.items():
        if path.suffix.lower() != ".json" or ROLE_CONTEXT.search(label) or label in refs:
            continue
        if path.name.lower() == "task.json":
            continue
        try:
            data = json.loads(_decode(path.read_bytes()))
        except (UnicodeError, ValueError):
            continue
        entries = [(ref, value) for kind, ref, value in _case_items(data, label + "#", root=True) if kind == "inline"]
        for ref, value in entries:
            inline[ref] = value
        if label in selected and entries:
            pos = selected.index(label)
            selected[pos:pos + 1] = [ref for ref, _ in entries]

    if sources:
        selected = []
        issues = [item for item in issues if item["code"] != "missing_declared_manuscript"]
        for source in sources:
            if source in inline:
                selected.append(source)
            elif any(k.startswith(source + "#/") for k in inline):
                selected.extend(k for k in inline if k.startswith(source + "#/"))
            else:
                add_path(source, "--source")
    elif not selected and not any(i.get("blocking") for i in issues):
        candidates = [name for name, p in files.items() if p.suffix.lower() in MANUSCRIPT
                      and p.name.lower() != "task.md" and name not in refs and not ROLE_CONTEXT.search(name)]
        named = [name for name in candidates if ROLE_DRAFT.search(name)]
        if inline and not named:
            selected = list(inline)
        elif len(named or candidates) == 1:
            selected = named or candidates
        elif named and all(re.search(r"chapter|section|章节", n, re.I) for n in named):
            def order(name):
                return tuple(int(s) if s.isdigit() else s.lower() for s in re.split(r"(\d+)", name))
            selected = sorted(named, key=order)
        elif candidates:
            issues.append({"code": "ambiguous_manuscripts", "candidates": named or candidates,
                           "message": "Use the task to select one or more --source paths.", "blocking": True})
    selected = list(dict.fromkeys(selected))
    lines, mapping, formats = [], [], []
    for name in selected:
        if name in inline:
            rows = [(s, {"location": f"line {n}"}) for n, s in enumerate(inline[name].splitlines(), 1)]
            fmt = "txt"
        else:
            p = _resolve(root, name)
            if not p:
                issues.append({"code": "missing_source", "source": name, "blocking": True})
                continue
            fmt = p.suffix.lower().lstrip(".")
            if p.suffix.lower() not in MANUSCRIPT:
                issues.append({"code": "unsupported_manuscript", "source": name, "blocking": True})
                continue
            rows = _read_rows(p, issues, name, True)
        if not rows:
            continue
        formats.append(fmt)
        if lines and lines[-1] and rows[0][0]:
            lines.append("")
            mapping.append({"source": name, "location": "source boundary"})
        for n, (line, loc) in enumerate(rows, 1):
            lines.append(line)
            mapping.append({"source": name, "source_line": n, **loc})

    for name, path in files.items():
        if name in selected or path.suffix.lower() not in MANUSCRIPT | CONTEXT:
            continue
        rows = _read_rows(path, issues, name, False)
        body = "\n".join(line for line, _ in rows)
        add_context(name, body)
        if re.search(r"glossar|terminolog|term.?table|词表|术语", name, re.I):
            try:
                if path.suffix.lower() == ".json":
                    add_terms(json.loads(body))
                elif path.suffix.lower() in {".csv", ".tsv"}:
                    sep = "\t" if path.suffix.lower() == ".tsv" else ","
                    add_terms(list(csv.DictReader(io.StringIO(body), delimiter=sep)))
            except (ValueError, csv.Error) as exc:
                issues.append({"code": "glossary_parse_error", "source": name, "message": str(exc)})
    fmt = formats[0] if len(set(formats)) == 1 else "md"
    trailing_newline = bool(lines)
    if len(selected) == 1 and lines:
        name = selected[0]
        if name in inline:
            trailing_newline = inline[name].endswith(("\r", "\n"))
        elif Path(name).suffix.lower() in TEXT:
            trailing_newline = _decode((root / name).read_bytes()).endswith(("\r", "\n"))
    text = "\n".join(lines) + ("\n" if trailing_newline else "")
    return {"text": text, "is_latex": fmt == "tex", "format": fmt, "line_map": mapping,
            "sources": selected, "context": context, "glossary": terms,
            "glossary_overrides": overrides, "warnings": issues}
