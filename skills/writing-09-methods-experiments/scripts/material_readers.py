"""Read experimental materials as passive data and keep precise, input-relative evidence."""
from __future__ import annotations

import ast
import csv
import io
import json
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


TEXT = {".txt", ".md", ".rst", ".log", ".py", ".sh", ".bash", ".r", ".tex",
        ".yaml", ".yml", ".json", ".jsonl", ".csv", ".tsv", ".toml", ".ini", ".cfg"}
CONTROL_FILES = {"task.md", "challenge.json", "resources.yaml", "case.json"}
CONTROL_KEYS = {"case_id", "challenge_id", "case_version", "output_contract", "instructions",
                "instruction", "task", "task_md", "task_description", "task_prompt", "prompt",
                "constraints", "rubric", "scoring", "requirements", "language", "output_language",
                "audience", "style", "format", "target_format", "deliverables", "output_format",
                "spec_file", "resource_manifest", "domain_label", "topic", "task_id", "expected_output"}
MAX_TEXT = 4 * 1024 * 1024
MAX_BINARY = 32 * 1024 * 1024
MAX_ITEMS = 220
MAX_SNIPPET = 2000
SCALAR = (str, int, float, bool)
ASSIGN = re.compile(r"""(?<![\w.-])([A-Za-z_]\w*)\s*=\s*("[^"\n]+"|'[^'\n]+'|[-+]?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?|[\w][\w./-]*)(?=$|[\s,;\]])""")
FLAG = re.compile(r"""(?:^|\s)--([A-Za-z][\w-]*)[=\s]+("[^"]+"|'[^']+'|[+-]?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?|[\w./-]+)(?=$|[\s;])""")
MEASUREMENT = re.compile(r"^(?:(?:train|val|test|eval|macro|micro|mean|std)_)*(?:loss|acc(?:uracy)?|precision|recall|f1|auc|mse|rmse|mae|r2|score|time|step|iteration|epoch)$", re.I)
SCOPE = re.compile(r"^(?:train(?:ing)?|val(?:idation)?|test(?:ing)?|eval(?:uation)?|baseline|control|ablation|(?:run|fold|seed|trial|group|baseline|model|condition)[_-]?[a-z0-9]+)$", re.I)
XML = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def pointer(parts):
    return "/" + "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in parts)


def input_files(root):
    for directory, dirs, files in os.walk(root, followlinks=False):
        base = Path(directory)
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d != "__pycache__" and not is_link(base / d))
        for name in sorted(files):
            path = base / name
            if not (name.startswith(".") or name.lower() in CONTROL_FILES or is_link(path)):
                yield path


def is_link(path):
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def read_text(path):
    data = path.read_bytes()
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("gb18030")


def docx_rows(path):
    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo("word/document.xml")
        if info.file_size > MAX_BINARY:
            raise ValueError("DOCX XML exceeds extraction budget")
        xml = archive.read(info)
    if b"<!DOCTYPE" in xml.upper():
        raise ValueError("DOCX with external/entity declarations requires review")
    body = ET.fromstring(xml).find(XML + "body")
    if body is None:
        return
    para = table = 0
    for element in body:
        if element.tag == XML + "p":
            para += 1
            yield f"para{para}", "".join(n.text or "" for n in element.iter(XML + "t"))
        elif element.tag == XML + "tbl":
            table += 1
            for row, tr in enumerate(element.findall(XML + "tr"), 1):
                cells = ["".join(n.text or "" for n in tc.iter(XML + "t")) for tc in tr.findall(XML + "tc")]
                yield f"table{table}.row{row}", " | ".join(cells)


def pdf_rows(path, issue):
    try:
        import fitz
    except ImportError:
        try:
            from pypdf import PdfReader
        except ImportError:
            program = shutil.which("pdftotext")
            if not program:
                raise ValueError("PDF extraction requires PyMuPDF, pypdf or pdftotext")
            # Read one page per subprocess so a single large document cannot monopolize memory.
            for n in range(1, 101):
                result = subprocess.run([program, "-f", str(n), "-l", str(n), "-enc", "UTF-8",
                                         str(path.resolve()), "-"], capture_output=True, timeout=10)
                if result.returncode:
                    if b"Wrong page range" not in result.stderr:
                        issue(f"PDF extraction stopped at page {n}; inspect the original")
                    break
                value = result.stdout.decode("utf-8", errors="replace").strip()
                if not value:
                    issue(f"page {n} has no text layer; inspect or OCR original page")
                yield f"p{n}", value
            else:
                issue("PDF page budget: inspect whether pages follow page 100")
            return
        doc = PdfReader(str(path))
        if doc.is_encrypted:
            raise ValueError("encrypted PDF")
        for i, page in enumerate(doc.pages, 1):
            if i > 100:
                issue("PDF page budget: read first 100 pages")
                break
            value = page.extract_text() or ""
            if not value.strip():
                issue(f"page {i} has no text layer; inspect or OCR original page")
            yield f"p{i}", value
        return
    with fitz.open(path) as doc:
        if doc.needs_pass:
            raise ValueError("encrypted PDF")
        if len(doc) > 100:
            issue("PDF page budget: read first 100 pages")
        for i in range(min(len(doc), 100)):
            blocks = [b for b in doc[i].get_text("blocks", sort=True) if b[6] == 0 and b[4].strip()]
            if not blocks:
                issue(f"page {i+1} has no text layer; inspect or OCR original page")
            for b in blocks:
                yield f"p{i+1}.b{b[5]+1}", b[4]


def tabular_rows(raw, delim):
    reader = csv.reader(io.StringIO(raw), delimiter=delim)
    headers = next(reader, [])
    if headers:
        yield "row1", " | ".join(headers), {}
    for index, values in enumerate(reader, 2):
        row = dict(zip((h.strip().lower() for h in headers), values))
        text = " | ".join(f"{h}={v}" for h, v in zip(headers, values))
        if len(values) > len(headers):
            text += " | " + " | ".join(values[len(headers):])
        yield f"row{index}", text, row


def source_scope(source, keys=(), line=""):
    scope = []
    parts = list(Path(source).parts[:-1])
    stem = Path(source).stem
    if re.search(r"(?:run|fold|trial|condition)[_-]?[a-z0-9]+", stem, re.I):
        parts.extend(re.split(r"[.]", stem))
    sequence = parts + list(keys)
    for index, part in enumerate(sequence):
        term = str(part)
        if index and str(sequence[index - 1]).lower() in {"runs", "folds", "trials", "conditions", "models", "groups", "phases"}:
            if term not in scope:
                scope.append(term)
        elif SCOPE.fullmatch(term) and term not in scope:
            scope.append(term)
    for match in re.finditer(r"\b(?:phase|stage|split|run_id|trial_id|group|condition)\s*[:=]\s*([\w.-]+)", line, re.I):
        if match[1] not in scope:
            scope.append(match[1])
    prefix = re.match(r"^\s*\[?(train|validation|val|test|eval)(?:\]|[ :])", line, re.I)
    if prefix and prefix[1] not in scope:
        scope.append(prefix[1])
    return ".".join(scope)


def code_literals(raw):
    """Read module-level literal defaults; expressions, imports and functions are never run."""
    try:
        module = ast.parse(raw)
    except (SyntaxError, ValueError, RecursionError):
        return {}
    found = {}
    for node in module.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, (ast.Constant, ast.UnaryOp)):
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and isinstance(value, SCALAR):
                    found.setdefault(node.lineno, []).append((target.id, value))
    return found


def line_pairs(line, suffix):
    if suffix not in {".txt", ".log", ".sh", ".bash", ".ini", ".cfg"} or line.lstrip().startswith("#"):
        return
    for pattern in (ASSIGN, FLAG):
        for match in pattern.finditer(line):
            name = match[1].replace("-", "_")
            if name.lower() in {"phase", "stage", "split", "run_id", "trial_id", "group", "condition", "config"}:
                continue
            if not MEASUREMENT.fullmatch(name):
                yield name, match[2].strip("\"'")
