"""Author–date markers. Resolve against supplied authors, dates and any explicit old bibliography."""
from __future__ import annotations

from collections import defaultdict
import re
import unicodedata

from io_utils import first_author_surname, normalize_title

LAST = r"(?:[A-ZÀ-Þ][\w'’\-]+|[\u3400-\u9fff]{2,6})"
AUTHOR = rf"{LAST}(?:[ \t]+et[ \t]+al\.?|等|[ \t]*(?:&|and|与|和)[ \t]*{LAST})?"
YEAR = r"(?:18|19|20)\d{2}[a-z]?"
LOC = r"(?:pp?\.|pages?|第)\s*\d+(?:\s*[-–—]\s*\d+)?(?:页)?"
AY = rf"{AUTHOR}[ \t]*[,，]?[ \t]*{YEAR}(?:[ \t]*[,，][ \t]*{LOC})?"
PAREN = rf"[(（](?P<names>{AY}(?:\s*[;；]\s*{AY})*)[)）]"
NARRATIVE = rf"(?P<narr_names>{AUTHOR})[ \t]*[(（](?P<narr_year>{YEAR})(?:[,，]\s*(?P<narr_loc>{LOC}))?[)）]"
ITEM = re.compile(rf"(?P<names>{AUTHOR})[ \t]*[,，]?[ \t]*(?P<year>{YEAR})(?:[ \t]*[,，][ \t]*(?P<loc>{LOC}))?$")


def _fold(name):
    return unicodedata.normalize("NFKC", name).strip().casefold().replace("’", "'")


def source_suffixes(bibliography, records):
    """Year letters can be mapped only to records identified in the manuscript's actual reference list."""
    suffixes = defaultdict(set)
    for line in bibliography.splitlines():
        year = re.search(r"\b((?:18|19|20)\d{2}[a-z])\b", line)
        if not year:
            continue
        title_line = " " + normalize_title(line) + " "
        matches = [r for r in records if r.get("title") and
                   (" " + normalize_title(r["title"]) + " " in title_line or
                    r.get("doi") and _fold(r["doi"]) in _fold(line))]
        if len(matches) == 1:
            suffixes[year[1]].add(matches[0]["cite_key"])
    return suffixes


def parse_marker(marker, records, suffixes):
    if marker["narrative"]:
        parts = [(marker["narr_names"], marker["narr_year"], marker["narr_loc"])]
    else:
        groups = [ITEM.fullmatch(item.strip()) for item in re.split(r"[;；]", marker["names"])]
        parts = [(g["names"], g["year"], g["loc"]) for g in groups]
    keys, locators = [], []
    for names, year, locator in parts:
        et_al = bool(re.search(r"(?:\s+et al\.?|等)$", names, re.I))
        people = re.split(r"\s+(?:and|&)\s+|[与和]", re.sub(r"\s+et al\.?$|等$", "", names))
        expected = [_fold(n) for n in people]
        candidates = []
        for rec in records:
            authors = [_fold(first_author_surname([a])) for a in rec.get("authors", [])]
            if str(rec.get("year")) != year[:4] or authors[:len(expected)] != expected:
                continue
            if et_al and len(authors) < 2:
                continue
            if not et_al and len(authors) != len(expected):
                continue
            if len(year) > 4 and rec["cite_key"] not in suffixes.get(year, set()):
                continue
            candidates.append(rec["cite_key"])
        keys.append(candidates[0] if len(candidates) == 1 else None)
        locators.append(locator)
    return keys, locators
