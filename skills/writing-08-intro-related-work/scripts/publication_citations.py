"""Render source-key citations into the actual manuscript and its evidence spans."""
from __future__ import annotations

import re
from collections import defaultdict

from citation_audit import Citations, REFERENCES, _surname, normalized


KEY = r"[\w][\w.:/+#-]*"
GROUP = re.compile(r"\[(?=[^\[\]\n]*?(?<![\w@])@" + KEY + r")[^\[\]\n]+\](?!\()")
ITEM = re.compile(r"\s*@(" + KEY + r")(?:\s*,\s*(.+?))?\s*$")
TEX = re.compile(r"\\(?P<cmd>cite|citep|citet|autocite|parencite|textcite)\{(?P<keys>[^{}\n]+)\}")
CODE = re.compile(r"(?ms)^`{3,}.*?^`{3,}[ \t]*$|^~{3,}.*?^~{3,}[ \t]*$|`[^`\n]+`")
ENTRY = re.compile(r"^\s*(?:(?:[-*]|\[(\d+)\]|(\d+)[.)、])\s+)?(.*)$")
LABEL = re.compile(r"\s*\[\s*@(" + KEY + r")\s*\]\s*[.]?\s*$")


def case_citation_style(case):
    """Use explicit case preferences first, including styles written in instructions."""
    fields = ("citation_style", "citation_format", "reference_style", "bibliography_style")
    containers = [case, *(x for x in (case.get("constraints"), case.get("style_guide")) if isinstance(x, dict))]
    explicit = " ".join(str(d[k]) for d in containers for k in fields if d.get(k))
    requested = explicit or " ".join(str(case.get(k) or "") for k in ("instructions", "requirements", "task_description"))
    if re.search(r"pandoc|biblatex|bibtex key|保留.{0,10}引用键|(?:keep|preserve) (?:source )?citation keys?", requested, re.I):
        return "source"
    if re.search(r"author[-–— /]*(?:date|year)|harvard|\bapa\s*\d*\b|作者.{0,4}(?:年[份代]?|出版年)", requested, re.I):
        return "author-year"
    return "numeric"


def _clean_bib_line(line):
    # Remove a visible working key at the end of a reference, including legacy backtick labels.
    line = re.sub(r"\s*\(`?@(" + KEY + r")`?\)\s*$", "", line)
    return LABEL.sub("", line).rstrip()


class PublicationCitations:
    """Use a single source-key map across the final manuscript and quoted manuscript spans."""

    def __init__(self, text, papers, style="numeric"):
        self.text, self.style = text, style
        self.papers = {p["cite_key"]: p for p in papers if isinstance(p, dict) and p.get("cite_key")}
        self.issues = []
        heading = REFERENCES.search(text)
        self.body = text[:heading.start()] if heading else text
        self.heading = heading.group().strip() if heading else ""
        self.bib = text[heading.end():] if heading else ""
        # Respect reference numbers supplied in the user's bibliography or metadata.
        citations = Citations(list(self.papers.values()), self.bib)
        self.numbers = {key: int(n) for n, key in citations.numbers.items()}
        assigned = [int(m.group(1)) for m in re.finditer(r"(?m)^\s*(?:\[|(?=\d))(\d+)(?:\]|[.)、])\s", self.bib)]
        self.next_number = 1 + max([0, *self.numbers.values(), *assigned])
        self.order = []
        self.used_keys = set()
        self.labels = defaultdict(list)
        for key, p in self.papers.items():
            self.labels[self._author_year(p)].append(key)
        self.active = style != "source" and bool(GROUP.search(self.body) or TEX.search(self.body))
        self.rendered_body = self._convert_segments(self.body, record=True) if self.active else self.body

    def issue(self, code, detail):
        issue = {"code": code, "detail": detail}
        if issue not in self.issues:
            self.issues.append(issue)

    @staticmethod
    def _author_year(paper):
        authors = paper.get("authors") or []
        if not isinstance(authors, list):
            authors = [authors]
        names = [_surname({"authors": [a]}) for a in authors]
        names = [n for n in names if n]
        short = (names[0] + " et al.") if len(names) > 2 else " & ".join(names)
        return short, str(paper.get("year") or "")

    def _render_keys(self, keys, original, record, narrative=False):
        unknown = [k for k, _ in keys if k not in self.papers]
        if unknown:
            if record:
                self.issue("unresolved_publication_citation", ", ".join(unknown))
            return original
        if not record and any(k not in self.used_keys for k, _ in keys):
            return original
        labels = []
        for key, loc in keys:
            if record and key not in self.used_keys:
                self.used_keys.add(key)
                self.order.append(key)
                if key not in self.numbers:
                    self.numbers[key] = self.next_number
                    self.next_number += 1
            author, year = self._author_year(self.papers[key])
            if self.style == "author-year" or narrative:
                if not author or (self.style == "author-year" and
                                  (not year or len(self.labels[author, year]) > 1)):
                    if record:
                        self.issue("ambiguous_publication_author_year", key)
                    return original
            if self.style == "author-year":
                label = f"{author} ({year})" if narrative else f"{author}, {year}"
            elif narrative:
                label = f"{author} [{self.numbers[key]}]"
            else:
                label = str(self.numbers[key])
            labels.append(label + (", " + loc if loc else ""))
        if narrative:
            return "; ".join(labels)
        if self.style == "author-year":
            return "(" + "; ".join(labels) + ")"
        return "[" + ("; " if any(loc for _, loc in keys) else ", ").join(labels) + "]"

    def _convert_segments(self, text, record=False):
        def convert(part):
            def group(match):
                items = re.split(r";|,(?=\s*@)", match.group()[1:-1])
                parsed = [ITEM.fullmatch(item) for item in items]
                if not all(parsed):
                    if record:
                        self.issue("review_publication_citation_syntax", match.group())
                    return match.group()
                return self._render_keys([(m[1], m[2]) for m in parsed], match.group(), record)

            def tex(match):
                keys = [(k.strip(), "") for k in match["keys"].split(",")]
                return self._render_keys(keys, match.group(), record, match["cmd"] in {"citet", "textcite"})

            # Keep numbering in order of first citation even when the two syntaxes are mixed.
            pieces, pos = [], 0
            for match in sorted([*GROUP.finditer(part), *TEX.finditer(part)], key=lambda m: m.start()):
                if match.start() < pos:
                    continue
                pieces.extend((part[pos:match.start()], group(match) if match.re is GROUP else tex(match)))
                pos = match.end()
            return "".join([*pieces, part[pos:]])
        pieces, pos = [], 0
        for token in CODE.finditer(text):
            pieces.extend((convert(text[pos:token.start()]), token.group()))
            pos = token.end()
        return "".join([*pieces, convert(text[pos:])])

    def fragment(self, text):
        if text == self.text:
            return self.render()[0]
        return self._convert_segments(text) if self.active and isinstance(text, str) else text

    def _reference(self, key):
        paper = self.papers[key]
        names = paper.get("authors") or []
        if not isinstance(names, list):
            names = [names]
        authors = []
        for a in names:
            if isinstance(a, dict):
                a = a.get("literal") or ", ".join(str(a[k]) for k in ("family", "given") if a.get(k))
            if a:
                authors.append(str(a))
        title = paper.get("title")
        if not title:
            self.issue("missing_reference_title", key)
        parts = [", ".join(authors), str(paper["year"]) if paper.get("year") else "", title, paper.get("venue")]
        if paper.get("doi"):
            parts.append(str(paper["doi"]))
        return ". ".join(str(p).strip().rstrip(".") for p in parts if p).rstrip(".") + "."

    def render(self):
        if not self.active or not self.order:
            if self.heading and self.style != "source":
                def remove_label(match):
                    if match[1] in self.papers:
                        return ""
                    self.issue("unresolved_publication_citation", match[1])
                    return match.group()
                cleaned = "\n".join(LABEL.sub(remove_label, line) for line in self.bib.split("\n"))
                if cleaned != self.bib:
                    return self.text[:-len(self.bib)] + cleaned, list(self.issues)
            return self.text, list(self.issues)
        refs = {}
        rest = []
        numbered_rest = []
        for line in self.bib.splitlines():
            if not line.strip():
                continue
            number1, number2, content = ENTRY.match(line).groups()
            marked = LABEL.search(content)
            hits = [marked[1]] if marked else [k for k in self.order
                        if self.papers[k].get("title") and normalized(self.papers[k]["title"]) in normalized(content)]
            if len(hits) == 1 and hits[0] in self.used_keys and hits[0] not in refs:
                refs[hits[0]] = _clean_bib_line(content)
            elif self.style == "numeric" and (number1 or number2):
                numbered_rest.append((int(number1 or number2), line))
            else:
                rest.append(line)
        if self.style == "numeric":
            numbered = []
            for key in self.order:
                n = self.numbers[key]
                numbered.append((n, f"[{n}] {refs.get(key) or self._reference(key)}"))
            entries = [line for _, line in sorted(numbered + numbered_rest)]
        else:
            entries = [refs.get(k) or self._reference(k) for k in self.order]
        heading = self.heading or ("## 参考文献" if re.search(r"[\u3400-\u9fff]", self.body) else "## References")
        result = self.rendered_body.rstrip() + "\n\n" + heading + "\n\n" + "\n\n".join(entries + rest) + "\n"
        return result, list(self.issues)


def clean_citations(text, papers, style="numeric"):
    return PublicationCitations(text, papers, style).render()
