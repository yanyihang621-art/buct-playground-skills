#!/usr/bin/env python3
"""Ingest heterogeneous academic sources (column-aware PDF, DOCX, BibTeX, RIS, JATS XML, JSON/JSONL, MD/TXT/TeX) into a unified index."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from itertools import islice
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from pdf_utils import write_json_atomic
from source_readers import (
    bib_entries,
    document_sections,
    docx_passages,
    jats_xml_passages,
    line_passages,
    pdf_structured_read,
    read_text,
    ris_entries,
    sheet_passages,
    single_paper_header,
)

TEXT_FORMATS = {".md", ".markdown", ".txt", ".tex", ".csv", ".tsv", ".yaml", ".yml", ".log"}
IGNORE_FILENAMES = {"challenge.json", "config.json", "resources.yaml"}
LITERATURE_KEYS = {"papers", "references", "bibliography", "literature", "literature_corpus", "reference_papers", "文献", "参考文献", "相关文献"}
STUDY_KEYS = {"study_facts", "author_study", "manuscript", "study_materials", "author_materials", "experiment_results", "own_study", "本研究", "作者材料", "实验结果", "手稿"}
INSTRUCTION_KEYS = {
    "task", "task_text", "task_description", "prompt", "instructions", "requirements",
    "constraints", "style", "style_guide", "audience", "goal", "language", "format",
    "output_format", "task_type", "citation_style", "deliverables", "request", "任务要求",
}
CONTROL_KEYS = {"output_contract", "input_contract", "schema", "case_id", "challenge_id", "domain_label", "resource_manifest", "corpus_file", "study_facts_file"}
PAPER_FIELDS = {
    "title": ("title", "paper_title", "题名", "标题", "篇名"),
    "authors": ("authors", "author", "作者"),
    "year": ("year", "publication_year", "issued", "年份", "出版年"),
    "venue": ("venue", "journal", "container-title", "booktitle", "publisher", "期刊", "会议"),
    "doi": ("doi",),
    "cite_key": ("cite_key", "citation_key", "citation-key", "bibkey", "id", "key", "引用键"),
    "route": ("route", "method_family", "技术路线"),
    "problem_setting": ("problem_setting", "problem", "问题设定"),
    "evaluation_system": ("evaluation_system", "evaluation", "评价体系"),
    "key_finding": ("key_finding", "finding", "核心发现"),
    "limitation": ("limitation", "limitations", "局限性"),
}


def file_role(path: Path) -> str:
    """Assign provisional role hint (never used as a hard lock against valid author/literature evidence)."""
    name = path.stem.casefold()
    parts = {p.casefold() for p in path.parts[:-1]}
    if name in {"task", "instructions", "requirements", "readme", "任务说明"}:
        return "instruction"
    if (
        re.search(r"manuscript|study[_-]?facts|own[_-]?study|author[_-]?draft|手稿|本文", name)
        or parts & {"study", "manuscript", "draft", "drafts", "author", "手稿", "草稿"}
    ):
        return "study"
    if (
        path.suffix.lower() in {".bib", ".ris"}
        or re.search(r"^papers?$|references|literature|bibliograph|survey|文献", name)
        or parts & {"papers", "references", "literature", "bibliography", "文献"}
    ):
        return "literature"
    return "material"


def field_role(key: str, role: str) -> str:
    k = key.casefold()
    if k in INSTRUCTION_KEYS or role == "instruction":
        return "instruction"
    if k in LITERATURE_KEYS:
        return "literature"
    if k in STUDY_KEYS:
        return "study"
    return role


def stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(stringify(x) for x in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def paper_record(item: Dict[str, Any], role: str) -> Optional[Dict[str, Any]]:
    fields = {str(k).casefold(): v for k, v in item.items()}
    row = {col: next((fields[k] for k in keys if fields.get(k)), None) for col, keys in PAPER_FIELDS.items()}
    if not row["title"] or role in ("instruction", "study"):
        return None
    if role != "literature" and not (row["doi"] or row["authors"] or (row["cite_key"] and row["year"])):
        return None
    authors = row["authors"] or []
    if isinstance(authors, str):
        authors = re.split(r"\s+and\s+|\s*[;；、]\s*", authors)
    elif not isinstance(authors, list):
        authors = [authors]
    author_names: List[str] = []
    for a in authors:
        if isinstance(a, dict):
            a = a.get("literal") or a.get("name") or " ".join(str(a[k]) for k in ("given", "family") if a.get(k))
        if a:
            author_names.append(str(a).strip())
    year_m = re.search(r"(?<!\d)[12]\d{3}(?!\d)", stringify(row["year"]))
    result: Dict[str, Any] = {k: stringify(v) for k, v in row.items()}
    result.update(authors=author_names, year=int(year_m[0]) if year_m else None, doi=stringify(row["doi"]) or None)
    return result


class MaterialIndex:
    def __init__(self, parse_work_dir: Path, ocr_mode: str = "auto") -> None:
        self.parse_work_dir = parse_work_dir
        self.ocr_mode = ocr_mode
        self.passages: List[Dict[str, Any]] = []
        self.sources: List[Dict[str, Any]] = []
        self.docs: List[Dict[str, Any]] = []
        self.papers: List[Dict[str, Any]] = []
        self.study_facts: Dict[str, Any] = {}
        self.material_gaps: List[Dict[str, Any]] = []
        self.review_notes: List[Dict[str, Any]] = []
        self.corpus_file: Optional[str] = None
        self.facts_file: Optional[str] = None

    def add(
        self,
        doc_id: str,
        path: str,
        locator: str,
        role: str,
        text: str,
        *,
        anchor: Optional[str] = None,
        kind: str = "block",
        page: Optional[int] = None,
        section_title: str = "Body",
    ) -> None:
        text = text.strip()
        if not text:
            return
        idx = len(self.passages) + 1
        sid = f"S{idx:04d}"
        anc = anchor or f"u{idx:04d}"
        self.passages.append({
            "source_id": sid,
            "doc_id": doc_id,
            "anchor": anc,
            "kind": kind,
            "page": page,
            "section_title": section_title,
            "path": path,
            "locator": locator,
            "role": role,
            "text": text,
        })

    def add_paper(self, paper: Optional[Dict[str, Any]], path: str, start: int, end: Optional[int] = None) -> None:
        if not paper:
            return
        ids = [p["source_id"] for p in self.passages[start:end]]
        doi = stringify(paper.get("doi")).lower().removeprefix("https://doi.org/")
        title = " ".join(paper["title"].casefold().split())
        for other in self.papers:
            same_title = title == " ".join(other["title"].casefold().split())
            other_doi = stringify(other.get("doi")).lower().removeprefix("https://doi.org/")
            both_formal = other.get("key_origin") not in {"generated", "file_stem"} and paper.get("key_origin") not in {"generated", "file_stem"}
            contradictory = both_formal and bool(other.get("year") and paper.get("year") and str(other["year"]) != str(paper["year"]))
            same_work = (doi == other_doi) if doi and other_doi else (same_title and not contradictory)
            if same_work:
                other_inferred = other.get("key_origin") in {"generated", "file_stem"}
                paper_inferred = paper.get("key_origin") in {"generated", "file_stem"}
                if other_inferred and not paper_inferred:
                    if (
                        paper.get("cite_key")
                        and paper["cite_key"] != other["cite_key"]
                        and all(p["cite_key"] != paper["cite_key"] for p in self.papers)
                    ):
                        other.setdefault("aliases", []).append(other["cite_key"])
                        other["cite_key"] = paper["cite_key"]
                    other["key_origin"] = paper.get("key_origin") or "supplied"
                    for bib_key in ("title", "authors", "year", "venue", "doi"):
                        if paper.get(bib_key):
                            other[bib_key] = paper[bib_key]
                elif paper_inferred and not other_inferred:
                    if paper.get("cite_key") and paper["cite_key"] != other["cite_key"]:
                        other.setdefault("aliases", []).append(paper["cite_key"])
                    paper.pop("cite_key", None)
                    paper.pop("key_origin", None)
                for key, value in paper.items():
                    if not other.get(key):
                        other[key] = value
                other["source_ids"].extend(ids)
                return

        if not paper.get("cite_key"):
            paper["key_origin"] = "generated"
        key = paper.get("cite_key") or f"paper_{len(self.papers) + 1}"
        used = {other["cite_key"] for other in self.papers}
        if key in used:
            n = 2
            while f"{key}_{n}" in used:
                n += 1
            key = f"{key}_{n}"
        paper.update(cite_key=key, source_ids=ids)
        self.papers.append(paper)
        if not self.corpus_file:
            self.corpus_file = path

    def register_facts(self, facts: Any, path: str) -> None:
        if not isinstance(facts, dict):
            return
        if not self.facts_file:
            self.facts_file = path
        for key, value in facts.items():
            if key in ("contributions", "target_gaps") and isinstance(value, list):
                value = [r for r in value if isinstance(r, dict)]
            if key not in self.study_facts:
                self.study_facts[key] = value

    def json_passages(self, doc_id: str, obj: Any, path: str, role: str, loc: str = "", *, allow_paper: bool = True) -> None:
        start = len(self.passages)
        if isinstance(obj, dict):
            paper = paper_record(obj, role) if allow_paper else None
            if paper:
                role = "literature"
            for key, value in obj.items():
                if key.casefold() in CONTROL_KEYS:
                    continue
                new_role = field_role(key, role)
                if key.casefold() in STUDY_KEYS:
                    self.register_facts(value, path)
                self.json_passages(doc_id, value, path, new_role, f"{loc}/{key}", allow_paper=allow_paper and not paper)
            self.add_paper(paper, path, start)
        elif isinstance(obj, list):
            for i, value in enumerate(obj):
                self.json_passages(doc_id, value, path, role, f"{loc}/{i}", allow_paper=allow_paper)
        elif obj is not None:
            anc = loc.strip("/").replace("/", "_") or "root"
            self.add(doc_id, path, loc or "/", role, stringify(obj), anchor=anc, section_title=loc or "JSON")

    def read_file(self, doc_id: str, path: Path, rel: str, role: str, case_data: Optional[Dict[str, Any]]) -> None:
        suf = path.suffix.lower()
        first = len(self.passages)
        if suf == ".json":
            obj = case_data if rel == "case.json" and case_data is not None else json.loads(read_text(path))
            if role == "study" and isinstance(obj, dict):
                self.register_facts(obj, rel)
            self.json_passages(doc_id, obj, rel, role)
        elif suf == ".jsonl":
            for number, line in enumerate(read_text(path).splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    self.add(doc_id, rel, f"line {number}", role, line, anchor=f"L{number}")
                    continue
                self.json_passages(doc_id, row, rel, role, f"L{number}")
        elif suf == ".bib":
            raw = read_text(path)
            parsed = False
            for fields, original in bib_entries(raw):
                start = len(self.passages)
                ck = fields["cite_key"]
                self.add(doc_id, rel, f"entry {ck}", "literature", original, anchor=f"entry_{ck}", section_title="Bibliography")
                self.add_paper(paper_record(fields, "literature"), rel, start)
                parsed = True
            if not parsed and raw.strip():
                for loc, passage in line_passages(raw):
                    self.add(doc_id, rel, loc, role, passage, anchor=loc)
        elif suf == ".ris":
            for i, (tags, original) in enumerate(ris_entries(read_text(path)), 1):
                start = len(self.passages)
                self.add(doc_id, rel, f"record {i}", "literature", original, anchor=f"ris_{i}", section_title="Bibliography")
                fields = {
                    "title": tags.get("TI") or tags.get("T1"),
                    "authors": tags.get("AU") or tags.get("A1"),
                    "year": tags.get("PY") or tags.get("Y1"),
                    "venue": tags.get("JO") or tags.get("JF") or tags.get("T2"),
                    "doi": next(iter(tags.get("DO", [])), None),
                    "id": next(iter(tags.get("ID", [])), None),
                }
                self.add_paper(paper_record(fields, "literature"), rel, start)
        elif suf == ".xml":
            meta, jats_rows = jats_xml_passages(path)
            for anc, sec, txt in jats_rows:
                self.add(doc_id, rel, anc, role, txt, anchor=anc, section_title=sec)
            if meta.get("title") and role != "study":
                rec = paper_record(meta, "literature")
                if rec:
                    rec.update(cite_key=path.stem, key_origin="file_stem")
                    self.add_paper(rec, rel, first)
        elif suf == ".pdf":
            doc = pdf_structured_read(path, self.parse_work_dir, ocr_mode=self.ocr_mode)
            if doc.get("status") in ("refused", "unverifiable") or doc.get("diagnostics", {}).get("encrypted"):
                self.material_gaps.append({"code": "unreadable_pdf", "path": rel, "message": "PDF is encrypted or unreadable."})
            tables_by_id = {t["table_id"]: t for t in doc.get("tables", []) if t.get("table_id")}
            emitted_tables = set()
            for blk in doc.get("blocks", []):
                if blk.get("role") in ("header", "footer"):
                    continue
                if blk.get("role") == "table_ref":
                    tid = blk.get("table_id")
                    tab = tables_by_id.get(tid or "")
                    if tab and tid not in emitted_tables:
                        emitted_tables.add(tid)
                        t_txt = "\n".join(filter(None, [tab.get("caption") or tab.get("label"), tab.get("markdown")]))
                        pg = (tab.get("pages") or [blk.get("page")])[0]
                        self.add(doc_id, rel, f"page {pg} {tid}", role, t_txt, anchor=tid, kind="table", page=pg, section_title=tab.get("caption") or "Table")
                    continue
                txt = (blk.get("structured_text") or blk.get("text") or "").strip()
                sec = " > ".join(blk.get("section_path") or []) or blk.get("role", "Body")
                pg = blk.get("page")
                self.add(doc_id, rel, f"page {pg} {blk['block_id']}", role, txt, anchor=blk["block_id"], page=pg, section_title=sec)
            for tid, tab in tables_by_id.items():
                if tid not in emitted_tables:
                    t_txt = "\n".join(filter(None, [tab.get("caption") or tab.get("label"), tab.get("markdown")]))
                    pg = (tab.get("pages") or [None])[0]
                    self.add(doc_id, rel, f"page {pg or '?'} {tid}", role, t_txt, anchor=tid, kind="table", page=pg, section_title=tab.get("caption") or "Table")
            # Register PDF paper metadata if available
            pmeta = doc.get("metadata", {})
            if pmeta.get("title") and role != "study" and not re.search(r"draft|manuscript|protocol|手稿", path.stem, re.I):
                front = "\n".join(p["text"] for p in self.passages[first:first + 6])
                m_doi = re.search(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)", front)
                m_yr = re.search(r"\b((?:19|20)\d{2})\b", front)
                cand = {
                    "title": pmeta["title"],
                    "authors": pmeta.get("authors") or [],
                    "year": m_yr.group(1) if m_yr else None,
                    "doi": m_doi.group(1).rstrip(".,;)]") if m_doi else None,
                }
                rec = paper_record(cand, "literature")
                if rec and (rec.get("authors") or rec.get("doi") or rec.get("year")):
                    if re.fullmatch(r"[\w.+-]+", path.stem):
                        rec.update(cite_key=path.stem, key_origin="file_stem")
                    self.add_paper(rec, rel, first)
        elif suf == ".docx":
            for anc, sec, txt in docx_passages(path):
                kind = "table" if anc.startswith("tab_") else "block"
                self.add(doc_id, rel, anc, role, txt, anchor=anc, kind=kind, section_title=sec)
        elif suf == ".xlsx":
            for anc, sec, txt in sheet_passages(path):
                self.add(doc_id, rel, anc, role, txt, anchor=anc, kind="table", section_title=sec)
        elif suf in TEXT_FORMATS:
            for loc, passage in line_passages(read_text(path)):
                for pos, section in (document_sections(loc, passage) if suf in {".txt", ".md", ".markdown"} else [(loc, passage)]):
                    self.add(doc_id, rel, pos, role, section, anchor=loc, section_title="Body")
        else:
            self.review_notes.append({"path": rel, "message": "Inspect non-text file with format-specific reader."})

        if (
            suf in {".txt", ".md", ".markdown", ".docx"}
            and role in {"literature", "material"}
            and not re.search(r"draft|manuscript|protocol|bibliograph|references?|^refs?$|草稿|手稿|文献目录|参考文献列表", path.stem, re.I)
        ):
            found = single_paper_header(self.passages[first:])
            if found:
                fields, count = found
                record = paper_record(fields, "literature")
                if record and re.fullmatch(r"[\w.+-]+", path.stem):
                    record.update(cite_key=path.stem, key_origin="file_stem")
                self.add_paper(record, rel, first, first + count)


def ingest_materials(
    input_dir: Path,
    case_data: Optional[Dict[str, Any]] = None,
    *,
    exclude: tuple = (),
    parse_work_dir: Optional[Path] = None,
    ocr_mode: str = "auto",
) -> Dict[str, Any]:
    root = Path(input_dir).resolve()
    pwork = parse_work_dir or (root.parent / ".w08_parsed")
    index = MaterialIndex(pwork, ocr_mode=ocr_mode)
    skip = {Path(x).resolve() for x in exclude}
    if case_data is None and (root / "case.json").is_file():
        try:
            cand = json.loads(read_text(root / "case.json"))
            if isinstance(cand, dict):
                case_data = cand
        except (OSError, ValueError):
            pass

    files = sorted(
        (
            p for p in root.rglob("*")
            if p.is_file()
            and not p.is_symlink()
            and p.name.lower() not in IGNORE_FILENAMES
            and all(not part.startswith(".") for part in p.relative_to(root).parts)
            and p.resolve().is_relative_to(root)
            and p.resolve() not in skip
        ),
        key=lambda p: p.relative_to(root).as_posix(),
    )
    if case_data is not None and not (root / "case.json").is_file():
        files.insert(0, root / "case.json")

    for idx_num, path in enumerate(files, 1):
        rel = path.relative_to(root).as_posix()
        role = file_role(Path(rel))
        if case_data:
            for hint, kind in (("corpus_file", "literature"), ("study_facts_file", "study")):
                val = case_data.get(hint)
                if isinstance(val, str) and (root / val).resolve() == path.resolve():
                    role = kind
        doc_id = f"doc_{idx_num}"
        start = len(index.passages)
        try:
            index.read_file(doc_id, path, rel, role, case_data)
        except Exception as exc:
            index.material_gaps.append({"code": "extraction_failed", "path": rel, "message": f"{type(exc).__name__}: {exc}"})
        pcount = len(index.passages) - start
        final_role = role
        if role == "material" and pcount > 0 and all(p["role"] == "literature" for p in index.passages[start:]):
            final_role = "literature"
        index.sources.append({
            "doc_id": doc_id,
            "path": rel,
            "format": path.suffix.lower().lstrip("."),
            "role": final_role,
            "passage_count": pcount,
        })
        index.docs.append({
            "doc_id": doc_id,
            "path": rel,
            "role": final_role,
            "passage_count": pcount,
        })

    roles = {p["role"] for p in index.passages}
    if not index.papers and not roles.intersection({"literature", "material"}):
        index.material_gaps.append({"code": "empty_or_missing_literature_corpus", "message": "No literature source discovered."})
    if not index.study_facts and not roles.intersection({"study", "material"}):
        index.material_gaps.append({"code": "missing_study_facts", "message": "No author study material discovered."})

    return {
        "schema_version": "writing-08-materials/v3",
        "corpus_file": index.corpus_file,
        "study_facts_file": index.facts_file,
        "paper_count": len(index.papers),
        "papers": index.papers,
        "study_facts": index.study_facts,
        "docs": index.docs,
        "sources": index.sources,
        "passages": index.passages,
        "material_gaps": index.material_gaps,
        "review_notes": index.review_notes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", default="/input")
    parser.add_argument("--case", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--index", type=Path)
    parser.add_argument("--find")
    parser.add_argument("--source")
    parser.add_argument("--limit", type=int, default=12)
    args = parser.parse_args()
    if args.index:
        res = json.loads(read_text(args.index))
    else:
        override = json.loads(read_text(args.case)) if args.case else None
        pwork = (args.out.parent / "parsed") if args.out else None
        res = ingest_materials(Path(args.input_dir), override, exclude=(args.out,) if args.out else (), parse_work_dir=pwork)
    if args.out:
        write_json_atomic(args.out, res)
    if args.find or args.source:
        needle = (args.find or "").casefold()
        matches = (
            p for p in res["passages"]
            if (not args.source or args.source in (p["source_id"], p["doc_id"], p["path"]))
            and needle in (p["path"] + " " + p["locator"] + " " + p["text"]).casefold()
        )
        for p in islice(matches, max(0, args.limit)):
            print(json.dumps(p, ensure_ascii=False))
    else:
        print(json.dumps({
            "sources": res["sources"],
            "paper_count": res["paper_count"],
            "passage_count": len(res["passages"]),
            "material_gaps": res["material_gaps"],
        }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
