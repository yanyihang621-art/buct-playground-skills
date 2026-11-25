#!/usr/bin/env python3
"""Stage editable inputs and give the writer anchored access to scientific source material."""
import argparse
import json
import os
import posixpath
import shutil
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

from tex_utils import write_json_atomic, write_text_lf

# Recognize implicit inline filename keys; filesystem projects can use arbitrary suffixes.
PROJECT_SUFFIXES = set(
    ".tex .ltx .bib .bst .cls .sty .clo .cfg .def .fd .bbx .cbx .lbx .bbl .ist "
    ".pdf .eps .jpg .jpeg .png .svg .tikz .pgf .csv .tsv .dat .table .txt .lua "
    ".ttf .otf .enc .map .icc .py .asy .gnuplot .code .ins .dtx .md .xml .json .yaml .yml".split()
)
CONTROL = {"case.json", "task.md", "challenge.json", "config.json", "resources.yaml"}
METADATA_DIRS = {".git", ".hg", ".svn", "__MACOSX", "__pycache__"}
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def safe_name(raw):
    name = PurePosixPath(raw.replace("\\", "/"))
    if not name.parts or name.is_absolute() or ".." in name.parts or any(":" in part for part in name.parts):
        raise ValueError("invalid relative member path")
    return name


def unpack(path, dest):
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > 5000 or sum(i.file_size for i in entries) > 256 * 1024**2:
            raise ValueError("archive exceeds staging budget; inspect selected files separately")
        for entry in entries:
            rel = safe_name(entry.filename)
            if stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError("unsupported archived link")
            if not entry.is_dir() and METADATA_DIRS.isdisjoint(rel.parts):
                target = dest / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as src, target.open("xb") as out:
                    shutil.copyfileobj(src, out)


def docx_text(path, *, stage_root=None, media_dir=None, images=None):
    with zipfile.ZipFile(path) as doc:
        body = ET.fromstring(doc.read("word/document.xml")).find(W + "body")
        if body is None:
            return ""
        lines = []
        # Word stores the editable document, image bytes and placement in separate parts.
        # Use only internal image relationships. Preserve linked-vs-embedded semantics.
        relationships = {}
        rels_part = "word/_rels/document.xml.rels"
        if rels_part in doc.namelist():
            for link in ET.fromstring(doc.read(rels_part)):
                if link.get("Type", "").endswith("/image") and link.get("TargetMode") != "External":
                    target = link.get("Target", "")
                    part = posixpath.normpath(posixpath.join("word", target)) if not target.startswith("/") else target[1:]
                    relationships[link.get("Id")] = safe_name(part).as_posix()
        stored = {}

        def read_images(block, location):
            for node in block.iter():
                key = node.get(R + "embed") or node.get(R + "id")
                part = relationships.get(key)
                if part is None:
                    continue
                lines.append(f"[figure at {location}] {part}")
                if stage_root is None or images is None:
                    continue
                if part not in stored:
                    record = {"source_part": part, "locations": []}
                    stored[part] = record
                    images.append(record)
                    try:
                        original = doc.getinfo(part)
                        if original.file_size > 256 * 1024**2:
                            raise ValueError("embedded image exceeds reader budget")
                        asset = media_dir / safe_name(part)
                        destination = stage_root / asset
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with doc.open(original) as raw, destination.open("xb") as out:
                            shutil.copyfileobj(raw, out)
                        record["reader_path"] = asset.as_posix()
                    except (KeyError, OSError, RuntimeError, ValueError, zipfile.BadZipFile) as exc:
                        record["detail"] = str(exc)
                if location not in stored[part]["locations"]:
                    stored[part]["locations"].append(location)

        para = 0
        table = 0
        for block in body:
            if block.tag == W + "p":
                para += 1
                lines.append(f"[paragraph {para}] " + "".join(n.text or "" for n in block.iter(W + "t")))
                read_images(block, f"paragraph {para}")
            elif block.tag == W + "tbl":
                table += 1
                for idx, row in enumerate(block.findall(W + "tr"), 1):
                    cells = ["".join(n.text or "" for n in cell.iter(W + "t")) for cell in row.findall(W + "tc")]
                    lines.append(f"[table {table} row {idx}] " + " | ".join(cells))
                    for column, cell in enumerate(row.findall(W + "tc"), 1):
                        read_images(cell, f"table {table} row {idx} cell {column}")
        return "\n".join(lines)


def pdf_text(path):
    try:
        import fitz
    except ImportError:
        tool = shutil.which("pdftotext")
        if not tool:
            raise ValueError("PDF extractor unavailable")
        result = subprocess.run([tool, "-layout", "-enc", "UTF-8", str(path), "-"],
                                capture_output=True, timeout=30, check=True)
        pages = result.stdout.decode("utf-8").split("\f")
    else:
        with fitz.open(path) as doc:
            pages = [p.get_text(sort=True) for p in doc]
    if not any(p.strip() for p in pages):
        raise ValueError("text layer empty; inspect page images or OCR")
    return "\n".join(f"[PDF page {n}]\n{p}" for n, p in enumerate(pages, 1) if p.strip())


def inline_files(case):
    """Recognize explicit filename/content pairs. Untyped scientific fields remain in case.json."""
    def walk(node, pointer):
        if isinstance(node, list):
            for idx, value in enumerate(node):
                yield from walk(value, f"{pointer}/{idx}")
        elif isinstance(node, dict):
            name = node.get("path") or node.get("filename") or node.get("name")
            content = node.get("content", node.get("text"))
            if isinstance(name, str) and isinstance(content, str) and (
                    "path" in node or "filename" in node or Path(name).suffix.lower() in PROJECT_SUFFIXES):
                yield name, content, pointer
            for key, value in node.items():
                if key == "output_contract":
                    continue
                ref = pointer + "/" + key.replace("~", "~0").replace("/", "~1")
                if Path(key).suffix.lower() in PROJECT_SUFFIXES and isinstance(value, str):
                    yield key, value, ref
                else:
                    yield from walk(value, ref)
    yield from walk(case, "")


def stage(input_dir, target):
    input_dir = input_dir.resolve(strict=True)
    target = target.resolve()
    if target == input_dir or input_dir in target.parents or target in input_dir.parents:
        raise ValueError("use a work directory separate from the input")
    if target.exists():
        raise FileExistsError(f"{target} already exists; resume its project or choose a fresh directory")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as temp:
        root = Path(temp) / "stage"
        baseline = root / "baseline"
        baseline.mkdir(parents=True)
        files = []
        warnings = []

        def add(path, source, rel, include):
            item = {"source": source}
            suffix = path.suffix.lower()
            # \input, listings and custom build commands may depend on filenames with
            # arbitrary extensions (including .pdf_tex) or no extension at all.
            if include:
                dest = baseline / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.exists():
                    raise ValueError(f"duplicate staged path {rel}")
                shutil.copy2(path, dest)
                item["project_path"] = rel.as_posix()
            if suffix in {".pdf", ".docx"}:
                try:
                    if suffix == ".docx":
                        images = []
                        value = docx_text(path, stage_root=root, media_dir=Path("reader") / f"{len(files) + 1:04}-media", images=images)
                        if images:
                            item["embedded_figures"] = images
                            warnings.extend({"source": f"{source}!/{image['source_part']}", "detail": image["detail"]}
                                            for image in images if "detail" in image)
                    else:
                        value = pdf_text(path)
                    view = Path("reader") / f"{len(files) + 1:04}.txt"
                    write_text_lf(root / view, value)
                    item["text_view"] = view.as_posix()
                except (ValueError, RuntimeError, OSError, KeyError, zipfile.BadZipFile, ET.ParseError, subprocess.SubprocessError) as exc:
                    warnings.append({"source": source, "detail": str(exc)})
            files.append(item)

        for path in sorted(input_dir.rglob("*")):
            if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(input_dir):
                continue
            rel = path.relative_to(input_dir)
            if not METADATA_DIRS.isdisjoint(rel.parts):
                continue
            source = rel.as_posix()
            if path.suffix.lower() == ".zip":
                try:
                    archive_dir = root / "unpacked" / rel
                    unpack(path, archive_dir)
                    for member in sorted(p for p in archive_dir.rglob("*") if p.is_file()):
                        entry = member.relative_to(archive_dir)
                        add(member, f"{source}!/{entry.as_posix()}", rel / entry, True)
                except (RuntimeError, ValueError, OSError, zipfile.BadZipFile) as exc:
                    warnings.append({"source": source, "detail": str(exc)})
                continue
            add(path, source, rel, source not in CONTROL)
        case_file = input_dir / "case.json"
        if case_file.is_file():
            case = json.loads(case_file.read_text(encoding="utf-8-sig"))
            for name, content, ref in inline_files(case):
                try:
                    rel = safe_name(name)
                except ValueError as exc:
                    warnings.append({"source": "case.json#" + ref, "detail": str(exc)})
                    continue
                if (baseline / rel).exists():
                    warnings.append({"source": "case.json#" + ref, "detail": f"also specified by another source: {rel}; reconcile"})
                else:
                    write_text_lf(baseline / rel, content)
                    files.append({"source": "case.json#" + ref, "project_path": rel.as_posix()})
        shutil.copytree(baseline, root / "project")
        write_json_atomic(root / "materials.json", {"files": files, "warnings": warnings})
        os.replace(root, target)
    return {"stage": str(target), "materials": len(files), "warnings": len(warnings)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path("/input"))
    parser.add_argument("--stage", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(stage(args.input_dir, args.stage), ensure_ascii=False))
