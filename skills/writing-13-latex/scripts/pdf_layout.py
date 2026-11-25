#!/usr/bin/env python3
"""Localize text cut by the PDF display boundary and select pages for visual review."""

from __future__ import annotations

from collections import defaultdict
from math import isfinite
import xml.etree.ElementTree as ET


def _sides(box, width, height):
    x0, y0, x1, y1 = box
    return {"left": -x0, "right": x1 - width, "top": -y0, "bottom": y1 - height}


def glyph_findings(page):
    """PyMuPDF's paint trace retains off-page glyphs omitted by ordinary text extraction."""
    width, height = page.cropbox.width, page.cropbox.height
    rows = defaultdict(list)
    traces = page.get_texttrace()
    if not any(max(_sides(s["bbox"], width, height).values()) > 1.5 for s in traces):
        return []
    for span in traces:
        # Invisible OCR and fully transparent paint are excluded.
        if span.get("type") not in (0, 1) or span.get("opacity", 1) < 0.05:
            continue
        dx, dy = span["dir"]
        for code, _, origin, box in span["chars"]:
            char = chr(code)
            if char.isspace() or not char.isprintable() or not all(isfinite(v) for v in box):
                continue
            x0, y0, x1, y1 = box
            if x1 <= x0 or y1 <= y0:
                continue
            baseline = round((origin[1] * dx - origin[0] * dy) / 1.5)
            rows[(round(dx, 2), round(dy, 2), baseline)].append((char, box))

    findings = []
    for glyphs in rows.values():
        visible_peer = any(x0 >= 0 and y0 >= 0 and x1 <= width and y1 <= height
                           for _, (x0, y0, x1, y1) in glyphs)
        lost = []
        edges = set()
        for char, box in glyphs:
            x0, y0, x1, y1 = box
            for side, depth in _sides(box, width, height).items():
                # Font ascent/descent and italic overhang can exceed the inked shape.
                extent = (x1 - x0) if side in ("left", "right") else (y1 - y0)
                if depth > max(1.5, 0.65 * extent):
                    edges.add(side)
                    lost.append((char, box))
                    break
        if not lost:
            continue
        evidence_box = (min(c[1][0] for c in lost), min(c[1][1] for c in lost),
                        max(c[1][2] for c in lost), max(c[1][3] for c in lost))
        sample = []
        previous = None
        for char, box in lost:
            if previous and box[0] - previous[2] > max(1.5, (box[3] - box[1]) * 0.35):
                sample.append(" ")
            sample.append(char)
            previous = box
        findings.append({
            "kind": "clipped_text_line" if visible_peer else "off_page_text",
            "page": page.number + 1, "edges": sorted(edges),
            "severity": "error" if visible_peer else "review",
            "glyph_count": len(lost), "text_sample": "".join(sample)[:120],
            "bounds_pt": [round(v, 2) for v in evidence_box],
            "display_size_pt": [round(width, 2), round(height, 2)],
        })
    return findings


def poppler_findings(xml_text):
    """Poppler can omit wholly off-page words; report the visible edge candidates for review."""
    findings = []
    for page_num, page in enumerate(ET.fromstring(xml_text).iterfind(".//{*}page"), 1):
        width, height = float(page.attrib["width"]), float(page.attrib["height"])
        for word in page.iterfind(".//{*}word"):
            box = [float(word.attrib[k]) for k in ("xMin", "yMin", "xMax", "yMax")]
            edges = [s for s, depth in _sides(box, width, height).items() if depth > 0.75]
            if edges:
                findings.append({
                    "kind": "word_at_display_edge", "severity": "review", "page": page_num,
                    "edges": edges, "text_sample": "".join(word.itertext())[:120],
                    "bounds_pt": [round(v, 2) for v in box],
                    "display_size_pt": [round(width, 2), round(height, 2)],
                })
    return findings


def preview_pages(page_count, findings, unresolved_pages, diagnostics):
    """Include early context and late problem pages; identify additional pages in the report."""
    pages = set(range(1, min(4, page_count) + 1))
    if page_count > 4:
        pages.add(page_count)
    flagged = [f["page"] for f in findings if f["severity"] == "error"]
    flagged += list(unresolved_pages)
    flagged += [f["page"] for f in findings if f["severity"] == "review"]
    if page_count and any(d["category"] in ("missing_glyph", "overfull_hbox", "overfull_vbox")
                          for d in diagnostics):
        flagged.append((page_count + 1) // 2)
    extra = [p for p in dict.fromkeys(flagged) if 1 <= p <= page_count and p not in pages]
    pages.update(extra[:8])
    return sorted(pages)
