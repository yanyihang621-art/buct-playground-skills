"""Resolve manuscript citations against supplied metadata; never infer numeric order."""

from __future__ import annotations

import re
import unicodedata


def normalized(value):
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


REFERENCES = re.compile(
    r"(?im)^\s*(?:#{1,6}\s+|\\(?:sub)*section\*?\{|\*\*)?(?:\d+[.)]?\s+)?"
    r"(?:references|bibliography|works cited|参考文献)\s*(?:\}|\*\*)?\s*$"
)
KEY_CITATION = re.compile(r"(?<![\w@])@([\w][\w.:/+#-]*)")
TEX_CITATION = re.compile(
    r"\\(?:cite(?:alp|alt|author|year|num|p|t)?|autocite|parencite|textcite|supercite)"
    r"\*?\s*(?:\[[^\]]*\]\s*){0,2}\{([^{}]+)\}", re.I
)
NUMBER_CITATION = re.compile(r"\[(\s*\d+(?:\s*[-–—,;，、]\s*\d+)*\s*)\](?!\()")
COMPARISON = re.compile(
    r"\b(?:whereas|unlike|both|compared (?:with|to)|by contrast|in contrast|"
    r"on the other hand|similarly|respectively|trade[- ]off|converge|differ(?:s|ed)?|"
    r"while|in common|in comparison|comparable|simultaneously|neither)\b|"
    r"相比|相较|相同|共同|均在|更适合|不同于|相反|相对而言|分别|前者|后者|二者|两者|而",
    re.I,
)


def _surname(paper):
    authors = paper.get("authors") or []
    first = authors[0] if isinstance(authors, list) and authors else authors
    if isinstance(first, dict):
        return str(first.get("family") or first.get("surname") or "")
    name = str(first).split(" and ")[0].split(",")[0].strip()
    words = name.split()
    if len(words) > 1 and all(len(w.strip(".")) <= 2 for w in words[1:]):
        return words[0]
    return words[-1] if words else ""


class Citations:
    def __init__(self, papers, bibliography):
        self.papers = {p["cite_key"]: p for p in papers if p.get("cite_key")}
        self.numbers = {}
        self.author_year = {}
        self.authors = []
        for key, paper in self.papers.items():
            number = str(paper.get("citation_number") or paper.get("reference_number") or "")
            if number.isdecimal():
                self.numbers[number] = key
            name, year = _surname(paper), str(paper.get("year") or "")
            if name and year:
                self.author_year.setdefault((normalized(name), year), []).append(key)
        for (name, year), matches in self.author_year.items():
            # English surnames need a word boundary, CJK names can touch surrounding prose.
            boundary = r"(?<![a-z])" if name.isascii() else ""
            surname = boundary + re.escape(name)
            coauthors = r"(?:\s+(?:et\s+al\.?|and\s+[\w’'-]+|&\s*[\w’'-]+)|\s*等)?"
            pattern = surname + coauthors + r"\s*[,，(（]?\s*" + re.escape(year) + r"(?![\w])"
            self.authors.append((re.compile(pattern, re.I), matches))
        for match in re.finditer(r"(?m)^\s*(?:\[(\d+)\]|(\d+)\s*[.)、])\s*(.+)$", bibliography):
            num, line = match.group(1) or match.group(2), normalized(match.group(3))
            keys = set()
            for key, p in self.papers.items():
                title, doi = normalized(p.get("title")), normalized(p.get("doi"))
                if ((title and title in line) or (doi and doi in line)
                        or re.search(r"@" + re.escape(key.casefold()) + r"(?![\w-])", line)):
                    keys.add(key)
            if len(keys) == 1:
                self.numbers[num] = keys.pop()

    def resolve(self, passage):
        keys = {m.group(1).rstrip(".,:") for m in KEY_CITATION.finditer(passage)}
        for match in TEX_CITATION.finditer(passage):
            keys.update(k.strip() for k in match.group(1).split(",") if k.strip())
        ambiguous = set()
        numeric = set()
        for match in NUMBER_CITATION.finditer(passage):
            for spec in re.split(r"[,;，、]", match.group(1)):
                ends = re.split(r"[-–—]", spec.strip())
                first, last = int(ends[0]), int(ends[-1])
                if last < first or last - first > 200:
                    ambiguous.add(match.group(0))
                    continue
                numeric.update(str(i) for i in range(first, last + 1))
        missing_numbers = numeric.difference(self.numbers)
        keys.update(self.numbers[n] for n in numeric if n in self.numbers)
        for pattern, candidates in self.authors:
            for match in pattern.finditer(passage):
                if len(candidates) == 1:
                    keys.add(candidates[0])
                else:
                    ambiguous.add(match.group(0))
        for match in re.finditer(r"\b([A-Z][^\W\d_]*(?:[-’'][^\W\d_]+)*)\s+et\s+al\.?"
                                 r"\s*[,（(]?\s*((?:19|20)\d{2}[a-z]?)\b", passage):
            if (normalized(match[1]), match[2]) not in self.author_year:
                ambiguous.add(match.group(0))
        return keys, missing_numbers, ambiguous
