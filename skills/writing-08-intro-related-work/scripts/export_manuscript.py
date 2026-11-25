"""Export requested manuscript files and render the actual Office document for its preview."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from rw_utils import write_text_lf
from output_contract import contract_specs


EXT = re.compile(r"(?<![a-z0-9])(docx|pdf|tex|png|md)(?![a-z0-9])", re.I)


def case_exports(case: dict) -> tuple[set[str], dict]:
    """Read explicitly named output formats/files. Do not treat input file extensions as requests."""
    formats, names = set(), {}

    def visit(value, filename=False):
        if isinstance(value, (list, tuple)):
            for element in value:
                visit(element, filename)
        elif isinstance(value, dict):
            for key, item in value.items():
                if key in {"name", "path", "file", "filename", "output_filename"}:
                    visit(item, True)
                elif key in {"format", "formats", "type", "files", "artifacts", "outputs", "media_type"}:
                    visit(item)
                elif str(key).lower() in {"md", "docx", "pdf", "png", "tex"}:
                    formats.add(key.lower())
                    visit(item, True)
        elif isinstance(value, str):
            token = value.strip().removeprefix("/app/output/")
            ext = Path(token).suffix.lower().lstrip(".")
            if (filename or " " not in token) and ext in {"docx", "pdf", "tex", "png", "md"}:
                names[ext] = token
                formats.add(ext)
            else:
                formats.update(m.group(1).lower() for m in EXT.finditer(token))
                if token.lower() in ("latex", "word", "markdown"):
                    formats.add({"latex":"tex", "word":"docx", "markdown":"md"}[token.lower()])

    for key in ("output_format", "output_formats", "output_filename", "output_files", "required_files",
                "required_artifacts", "expected_artifacts", "deliverables"):
        visit(case.get(key), filename=key.endswith(("filename", "files", "artifacts")))
    contract = case.get("output_contract")
    specs = contract_specs(contract)
    for name, spec in specs.items():
        if isinstance(contract, dict) and "properties" in contract and "required" in contract:
            if name not in contract["required"]:
                continue
        visit(name)
        if isinstance(spec, dict):
            visit(spec, True)
        elif isinstance(spec, str) and (EXT.fullmatch(spec.strip()) or Path(spec).suffix.lower() in (".docx", ".pdf", ".png", ".tex", ".md")):
            visit(spec, True)
    if isinstance(contract, dict):
        for key in ("output_format", "output_formats", "output_files", "required_artifacts", "deliverables"):
            visit(contract.get(key), True)
    return formats, names


def _run(args: list[str], work: Path) -> None:
    proc = subprocess.run(args, cwd=work, capture_output=True, timeout=45)
    if proc.returncode:
        msg = proc.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise ValueError(f"{Path(args[0]).name} exited {proc.returncode}. " + " ".join(msg[-2:])[:400])


def _target(out: Path, names: dict, ext: str) -> Path:
    name = names.get(ext, f"intro_and_related_work.{ext}")
    dest = (out / str(name)).resolve()
    if not dest.is_relative_to(out.resolve()) or dest.suffix.lower() != "." + ext:
        raise ValueError(f"Invalid {ext} artifact path: {name}")
    if dest == (out / "comparison_matrix.md").resolve():
        raise ValueError("Artifact path collides with comparison_matrix.md")
    return dest


def _tool(*names: str) -> str:
    for name in names:
        if path := shutil.which(name):
            return path
    if "soffice" in names and (root := os.environ.get("ProgramFiles")):
        path = Path(root) / "LibreOffice" / "program" / "soffice.exe"
        if path.is_file():
            return str(path)
    if "soffice" in names:
        for location in ("/usr/lib/libreoffice/program/soffice", "/usr/lib64/libreoffice/program/soffice",
                         "/opt/libreoffice/program/soffice", "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
            if Path(location).is_file() and os.access(location, os.X_OK):
                return location
        for location in sorted(Path("/opt").glob("libreoffice*/program/soffice")):
            if location.is_file() and os.access(location, os.X_OK):
                return str(location)
    raise ValueError("Missing executable " + "/".join(names))


def _prose_docx(source: Path, dest: Path) -> None:
    """Build basic editable scholarly prose when the fuller Pandoc writer is unavailable."""
    try:
        from docx import Document
    except ImportError as exc:
        raise ValueError("DOCX export requires Pandoc or python-docx") from exc
    doc = Document()
    text = source.read_text(encoding="utf-8")
    if re.search(r"(?m)^(\s*[|>]|\\begin\{|~~~|\x60\x60\x60)|!\[|\$\$|\\\(", text):
        raise ValueError("The manuscript uses tables, math or embedded media requiring the Pandoc DOCX writer")
    blocks = re.split(r"\n\s*\n|(?m:(?<=\n)(?=#{1,6} |[-*] |\d+[.)] ))", text)
    for block in blocks:
        for item in re.split(r"(?m)(?<=\n)(?=[-*] |\d+[.)] )", block.strip()):
            if not item:
                continue
            lines = item.splitlines()
            header = re.match(r"^(#{1,6})\s+(.+)$", lines[0])
            if header:
                doc.add_heading(header[2], level=len(header[1]))
                lines = lines[1:]
                if not lines:
                    continue
            content = " ".join(line.strip() for line in lines)
            bullet = re.match(r"^([-*]|\d+[.)])\s+(.+)", content)
            paragraph = doc.add_paragraph(style=("List Bullet" if bullet[1] in ("-", "*") else "List Number")
                                          if bullet else None)
            for chunk in re.split(r"(\*\*[^*\n]+\*\*|\*[^*\n]+\*)", bullet[2] if bullet else content):
                if chunk.startswith("**") and chunk.endswith("**"):
                    paragraph.add_run(chunk[2:-2]).bold = True
                elif chunk.startswith("*") and chunk.endswith("*"):
                    paragraph.add_run(chunk[1:-1]).italic = True
                else:
                    paragraph.add_run(chunk)
    doc.save(dest)


def _convert(source: Path, dest: Path, work: Path, language: str) -> None:
    if dest.suffix == ".docx" and not shutil.which("pandoc"):
        _prose_docx(source, dest)
        return
    args = [_tool("pandoc"), "--from", "markdown-citations-raw_tex-raw_html", "--standalone"]
    if re.fullmatch(r"[a-zA-Z]{2,3}(?:-[a-zA-Z]{2,8})*", language):
        args += ["--metadata", "lang=" + language]
    if dest.suffix == ".pdf":
        args += ["--pdf-engine", _tool("xelatex"), "--pdf-engine-opt=-no-shell-escape"]
        if re.search(r"[\u3400-\u9fff]", source.read_text(encoding="utf-8")):
            fonts = subprocess.run([_tool("fc-list"), ":lang=zh", "family"], capture_output=True, timeout=5)
            families = {name.strip() for line in fonts.stdout.decode("utf-8", "replace").splitlines()
                        for name in line.split(",")}
            for family in ("Noto Serif CJK SC", "Source Han Serif SC", "SimSun", "Microsoft YaHei",
                           "FandolSong", "Noto Sans CJK SC"):
                if family in families:
                    args += ["-V", "CJKmainfont=" + family]
                    break
            else:
                raise ValueError("No verified Chinese main font for the requested PDF")
    else:
        args += ["--sandbox"]
    _run([*args, str(source), "-o", str(dest)], work)


def export_manuscript(text: str, output_dir: Path, formats: list[str], names: dict,
                      language: str) -> tuple[dict, list]:
    requested = {str(f).lower().lstrip(".") for f in [*formats, *names]}
    artifacts, issues = {}, []
    try:
        md = _target(output_dir, names, "md")
    except ValueError as exc:
        issues.append({"code": "invalid_artifact_name", "message": str(exc)})
        md = _target(output_dir, {}, "md")
    write_text_lf(md, text)
    artifacts["intro_and_related_work_md"] = md.relative_to(output_dir.resolve()).as_posix()
    unknown = requested - {"md", "docx", "tex", "pdf", "png"}
    if unknown:
        issues.append({"code": "unsupported_export_format", "formats": sorted(unknown)})
    if "docx" in requested or "png" in requested:
        requested.add("pdf")
    if not requested & {"docx", "tex", "pdf", "png"}:
        return artifacts, issues

    with tempfile.TemporaryDirectory(prefix=".render-", dir=output_dir) as tmp:
        stage = Path(tmp).resolve()
        for ext in ("docx", "tex", "pdf", "png"):
            if ext not in requested:
                continue
            try:
                target = _target(output_dir, names, ext)
                intermediate = stage / ("manuscript." + ext)
                if ext == "pdf" and "docx" in requested:
                    office = _tool("soffice", "libreoffice")
                    _run([office, "-env:UserInstallation=" + (stage / "profile").as_uri(),
                          "--headless", "--convert-to", "pdf", "--outdir", str(stage),
                          str(stage / "manuscript.docx")], stage)
                elif ext == "png":
                    _run([_tool("pdftoppm"), "-f", "1", "-l", "1", "-singlefile", "-r", "120",
                          "-png", str(stage / "manuscript.pdf"), str(stage / "manuscript")], stage)
                else:
                    _convert(md, intermediate, stage, language)
                if not intermediate.is_file() or not intermediate.stat().st_size:
                    raise ValueError(f"The renderer produced no {ext} file.")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(intermediate, target)
                artifacts["intro_and_related_work_" + ext] = target.relative_to(output_dir.resolve()).as_posix()
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                issues.append({"code": "artifact_export_failed", "format": ext, "message": str(exc)})
    return artifacts, issues
