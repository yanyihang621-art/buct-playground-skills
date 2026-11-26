#!/usr/bin/env python3
"""Build a searchable index of supplied source material and its native locators."""
from __future__ import annotations

import argparse
import json
import re
from itertools import islice
from pathlib import Path

from rw_utils import write_json_atomic
from source_readers import (bib_entries, document_sections, docx_passages, line_passages, pdf_passages,
                            read_text, ris_entries, sheet_passages, single_paper_header)

TEXT_FORMATS = {".md", ".markdown", ".txt", ".tex", ".csv", ".tsv", ".ris", ".yaml", ".yml", ".log"}
LITERATURE_KEYS = {"papers", "references", "bibliography", "literature", "literature_corpus",
                   "reference_papers", "文献", "参考文献", "相关文献"}
STUDY_KEYS = {"study_facts", "author_study", "manuscript", "study_materials", "author_materials",
              "experiment_results", "own_study", "本研究", "作者材料", "实验结果", "手稿"}
INSTRUCTION_KEYS = {"task", "task_text", "task_description", "prompt", "instructions", "requirements",
                    "constraints", "style", "style_guide", "audience", "goal", "language", "format",
                    "output_format", "task_type", "citation_style", "deliverables", "request", "任务要求"}
CONTROL_KEYS = {"output_contract", "input_contract", "schema", "case_id", "challenge_id", "domain_label",
                "resource_manifest", "corpus_file", "study_facts_file"}
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


def file_role(path):
    name = path.stem.casefold()
    parts = set(p.casefold() for p in path.parts[:-1])
    if name in {"task", "instructions", "requirements", "readme", "任务说明"}:
        return "instruction"
    if (re.search(r"manuscript|study[_-]?facts|experiment|手稿|实验|本文", name)
            or parts & {"study", "manuscript", "draft", "drafts", "experiments", "author", "results", "手稿", "草稿"}):
        return "study"
    if (path.suffix.lower() in {".bib", ".ris"} or re.search(r"^papers?$|references|literature|bibliograph|文献", name)
            or parts & {"papers", "references", "literature", "bibliography", "文献"}):
        return "literature"
    return "material"


def field_role(key, role):
    key = key.casefold()
    if key in INSTRUCTION_KEYS or role == "instruction":
        return "instruction"
    if key in LITERATURE_KEYS:
        return "literature"
    if key in STUDY_KEYS:
        return "study"
    return role


def pointer(base, key):
    return base + "/" + str(key).replace("~", "~0").replace("/", "~1")


def stringify(value):
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(stringify(x) for x in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def paper_record(item, role):
    fields = {str(k).casefold(): v for k, v in item.items()}
    row = {col: next((fields[k] for k in keys if fields.get(k)), None)
           for col, keys in PAPER_FIELDS.items()}
    if not row["title"] or role == "instruction" or role == "study":
        return None
    if role != "literature" and not (row["doi"] or row["authors"] or row["cite_key"] and row["year"]):
        return None
    authors = row["authors"] or []
    if isinstance(authors, str):
        authors = re.split(r"\s+and\s+|\s*[;；、]\s*", authors)
    elif not isinstance(authors, list):
        authors = [authors]
    author_names = []
    for author in authors:
        if isinstance(author, dict):
            author = (author.get("literal") or author.get("name")
                      or " ".join(str(author[k]) for k in ("given", "family") if author.get(k)))
        if author:
            author_names.append(str(author).strip())
    year = re.search(r"(?<!\d)[12]\d{3}(?!\d)", stringify(row["year"]))
    result = {k: stringify(v) for k, v in row.items()}
    result.update(authors=author_names, year=int(year[0]) if year else None, doi=stringify(row["doi"]) or None)
    return result


class MaterialIndex:
    def __init__(self):
        self.passages = []
        self.sources = []
        self.papers = []
        self.study_facts = {}
        self.material_gaps = []
        self.review_notes = []
        self.corpus_file = None
        self.facts_file = None

    def add(self, path, locator, role, text):
        """Keep exact source text within each segment and cap model-facing unit size."""
        text = text.strip()
        distance = 3500
        offset = 0
        while offset < len(text):
            stop = min(len(text), offset + distance)
            if stop != len(text):
                boundary = max(text.rfind("\n", offset + distance // 2, stop),
                               text.rfind(" ", offset + distance // 2, stop))
                if boundary >= 0:
                    stop = boundary + 1
            loc = locator if len(text) <= distance else f"{locator}, chars {offset + 1}-{stop}"
            self.passages.append({"source_id": f"S{len(self.passages) + 1:04d}", "path": path,
                                  "locator": loc, "role": role, "text": text[offset:stop]})
            offset = stop

    def add_paper(self, paper, path, start, end=None):
        if not paper:
            return
        ids = [p["source_id"] for p in self.passages[start:end]]
        doi = stringify(paper["doi"]).lower().removeprefix("https://doi.org/")
        title = " ".join(paper["title"].casefold().split())
        for other in self.papers:
            same_title = title == " ".join(other["title"].casefold().split())
            other_doi = stringify(other["doi"]).lower().removeprefix("https://doi.org/")
            contradictory = any(other.get(k) and paper.get(k) and other[k] != paper[k] for k in ("year", "authors"))
            same_work = (doi == other_doi) if doi and other_doi else same_title and not contradictory
            if same_work:
                if (other.get("key_origin") in {"generated", "file_stem"} and paper.get("cite_key")
                        and not paper.get("key_origin") and paper["cite_key"] != other["cite_key"]
                        and all(p["cite_key"] != paper["cite_key"] for p in self.papers)):
                    other.setdefault("aliases", []).append(other["cite_key"])
                    other.update(cite_key=paper["cite_key"], key_origin="supplied")
                elif paper.get("key_origin") in {"generated", "file_stem"}:
                    if paper.get("cite_key") and paper["cite_key"] != other["cite_key"]:
                        other.setdefault("aliases", []).append(paper["cite_key"])
                    paper.pop("cite_key", None)
                    paper.pop("key_origin", None)
                for key, value in paper.items():
                    if not other.get(key):
                        other[key] = value
                    elif value and other[key] != value and key in {"cite_key", "year", "doi", "title"}:
                        self.review_notes.append({"path": path, "message": f"Verify alternate {key} for {other['cite_key']}: {value}"})
                other["source_ids"].extend(ids)
                return
        if not paper["cite_key"]:
            paper["key_origin"] = "generated"
        key = paper["cite_key"] or f"paper_{len(self.papers) + 1}"
        used = {other["cite_key"] for other in self.papers}
        if key in used:
            self.review_notes.append({"path": path, "message": f"Distinct works share citation key {key}. Check original references."})
            n = 2
            while f"{key}_{n}" in used:
                n += 1
            key = f"{key}_{n}"
        paper.update(cite_key=key, source_ids=ids)
        self.papers.append(paper)
        if not self.corpus_file:
            self.corpus_file = path

    def register_facts(self, facts, path):
        if not isinstance(facts, dict):
            return
        if not self.facts_file:
            self.facts_file = path
        for key, value in facts.items():
            if key in ("contributions", "target_gaps"):
                value = [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []
            if key not in self.study_facts:
                self.study_facts[key] = value
            elif self.study_facts[key] != value:
                self.review_notes.append({"path": path, "message": f"Multiple versions of study field {key}; compare located source values."})

    def json_passages(self, obj, path, role, loc="", *, allow_paper=True):
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
                self.json_passages(value, path, new_role, pointer(loc, key), allow_paper=allow_paper and not paper)
            self.add_paper(paper, path, start)
        elif isinstance(obj, list):
            for i, value in enumerate(obj):
                self.json_passages(value, path, role, pointer(loc, i), allow_paper=allow_paper)
        elif obj is not None:
            self.add(path, loc or "/", role, stringify(obj))

    def read_file(self, path, rel, role, case_data):
        suffix = path.suffix.lower()
        first = len(self.passages)
        if suffix == ".json":
            obj = case_data if rel == "case.json" and case_data is not None else json.loads(read_text(path))
            if role == "study" and isinstance(obj, dict):
                self.register_facts(obj, rel)
            self.json_passages(obj, rel, role)
        elif suffix == ".jsonl":
            for number, line in enumerate(read_text(path).splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    self.review_notes.append({"path": rel, "locator": f"line {number}", "message": "Malformed JSONL record; inspect the raw text."})
                    self.add(rel, f"line {number}", role, line)
                    continue
                self.json_passages(row, rel, role, f"line {number}")
        elif suffix == ".bib":
            raw = read_text(path)
            parsed = False
            for fields, original in bib_entries(raw):
                start = len(self.passages)
                self.add(rel, "entry " + fields["cite_key"], "literature", original)
                self.add_paper(paper_record(fields, "literature"), rel, start)
                parsed = True
            if not parsed and raw.strip():
                for loc, passage in line_passages(raw):
                    self.add(rel, loc, role, passage)
                self.review_notes.append({"path": rel, "message": "Review unparsed bibliography as raw text."})
        elif suffix == ".ris":
            for i, (tags, original) in enumerate(ris_entries(read_text(path)), 1):
                start = len(self.passages)
                self.add(rel, f"record {i}", "literature", original)
                fields = {"title": tags.get("TI") or tags.get("T1"), "authors": tags.get("AU") or tags.get("A1"),
                          "year": tags.get("PY") or tags.get("Y1"), "venue": tags.get("JO") or tags.get("JF") or tags.get("T2"),
                          "doi": next(iter(tags.get("DO", [])), None), "id": next(iter(tags.get("ID", [])), None)}
                self.add_paper(paper_record(fields, "literature"), rel, start)
        elif suffix in TEXT_FORMATS:
            for loc, passage in line_passages(read_text(path)):
                segments = document_sections(loc, passage) if suffix in {".txt", ".md", ".markdown"} else [(loc, passage)]
                for pos, section in segments:
                    self.add(rel, pos, role, section)
        elif suffix in (".docx", ".pdf", ".xlsx"):
            reader = {".docx": docx_passages, ".pdf": pdf_passages, ".xlsx": sheet_passages}[suffix]
            for loc, passage in reader(path):
                if suffix == ".pdf" and not passage.strip():
                    self.review_notes.append({"path": rel, "locator": loc, "message": "No PDF text layer. Inspect rendering or OCR this page."})
                for pos, section in document_sections(loc, passage):
                    self.add(rel, pos, role, section)
        else:
            self.review_notes.append({"path": rel, "message": "Inspect original file with a format-specific reader."})
        if (suffix in {".txt", ".md", ".markdown", ".pdf", ".docx"} and role in {"literature", "material"}
                and not re.search(r"draft|manuscript|protocol|bibliograph|references?|^refs?$|"
                                  r"草稿|手稿|题记|文献目录|参考文献列表|会议纪要", path.stem, re.I)):
            found = single_paper_header(self.passages[first:])
            if found:
                fields, count = found
                record = paper_record(fields, "literature")
                if re.fullmatch(r"[\w.+-]+", path.stem):
                    record.update(cite_key=path.stem, key_origin="file_stem")
                self.add_paper(record, rel, first, first + count)
                for unit in self.passages[first:first + count]:
                    unit["role"] = "literature"


def ingest_materials(input_dir: Path, case_data: dict | None = None, *, exclude=()) -> dict:
    root = Path(input_dir).resolve()
    index = MaterialIndex()
    skip = {Path(x).resolve() for x in exclude}
    if case_data is None and (root / "case.json").is_file():
        try:
            candidate = json.loads(read_text(root / "case.json"))
            if isinstance(candidate, dict):
                case_data = candidate
        except (OSError, ValueError):
            pass  # read_file reports the parse failure with its path.
    files = sorted((p for p in root.rglob("*") if p.is_file() and not p.is_symlink()
                    and all(not part.startswith(".") for part in p.relative_to(root).parts)
                    and p.resolve().is_relative_to(root) and p.resolve() not in skip),
                   key=lambda p: p.relative_to(root).as_posix())
    if case_data is not None and not (root / "case.json").is_file():
        files.insert(0, root / "case.json")
    for path in files:
        rel = path.relative_to(root).as_posix()
        role = file_role(Path(rel))
        if case_data:
            for hint, kind in (("corpus_file", "literature"), ("study_facts_file", "study")):
                value = case_data.get(hint)
                if isinstance(value, str) and (root / value).resolve() == path.resolve():
                    role = kind
        start = len(index.passages)
        try:
            index.read_file(path, rel, role, case_data)
        except (OSError, UnicodeError, ValueError, RuntimeError, KeyError, ImportError) as exc:
            index.material_gaps.append({"code": "unreadable_source", "path": rel, "message": str(exc)})
        except Exception as exc:
            # PDF and ZIP readers expose dependency-specific error classes.
            index.material_gaps.append({"code": "extraction_failed", "path": rel,
                                        "message": f"{type(exc).__name__}: {exc}"})
        index.sources.append({"path": rel, "format": path.suffix.lower().lstrip("."),
                              "role": "literature" if role == "material" and len(index.passages) > start and
                              all(p["role"] == "literature" for p in index.passages[start:]) else role,
                              "passage_count": len(index.passages) - start})
    roles = {p["role"] for p in index.passages}
    if not index.papers and not roles.intersection({"literature", "material"}):
        index.material_gaps.append({"code": "empty_or_missing_literature_corpus", "message": "No literature source discovered."})
    if not index.study_facts and not roles.intersection({"study", "material"}):
        index.material_gaps.append({"code": "missing_study_facts", "message": "No author study material discovered."})
    return {"schema_version": "writing-08-materials/v2", "corpus_file": index.corpus_file,
            "study_facts_file": index.facts_file, "paper_count": len(index.papers), "papers": index.papers,
            "study_facts": index.study_facts, "sources": index.sources, "passages": index.passages,
            "material_gaps": index.material_gaps, "review_notes": index.review_notes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", default="/input")
    parser.add_argument("--case", type=Path)
    parser.add_argument("--out", type=Path, help="Write the complete source index here.")
    parser.add_argument("--index", type=Path, help="Search an already-built index.")
    parser.add_argument("--find", help="Literal case-insensitive text or locator search.")
    parser.add_argument("--source", help="Exact input-relative path or source ID.")
    parser.add_argument("--limit", type=int, default=12, help="Maximum matching passages to print.")
    args = parser.parse_args()
    if args.index:
        res = json.loads(read_text(args.index))
    else:
        override = json.loads(read_text(args.case)) if args.case else None
        res = ingest_materials(Path(args.input_dir), override, exclude=[args.out] if args.out else [])
    if args.out:
        write_json_atomic(args.out, res)
    if args.find or args.source:
        needle = (args.find or "").casefold()
        matches = (p for p in res["passages"] if (not args.source or args.source in (p["source_id"], p["path"]))
                   and needle in (p["path"] + " " + p["locator"] + " " + p["text"]).casefold())
        for passage in islice(matches, max(0, args.limit)):
            print(json.dumps(passage, ensure_ascii=False))
    else:
        print(json.dumps({"sources": res["sources"], "paper_count": res["paper_count"],
                          "passage_count": len(res["passages"]), "material_gaps": res["material_gaps"],
                          "review_notes": res["review_notes"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
