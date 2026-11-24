#!/usr/bin/env python3
"""Scientific table parser: lattice, PyMuPDF, three-line (booktabs) multi-header, cross-page merger, and image tables."""

from __future__ import annotations

import argparse
import contextlib
import html as html_lib
import io
import re
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from pdf_utils import bbox_iou, bbox_overlap_ratio, bbox_union, normalize_ligatures, round_bbox, write_json_atomic

try:
    import fitz  # type: ignore
except ImportError:
    fitz = None

try:
    import pdfplumber  # type: ignore
except ImportError:
    pdfplumber = None

try:
    from PIL import Image  # type: ignore
except ImportError:
    Image = None  # type: ignore

TABLE_CAPTION_RE = re.compile(
    r"^\s*(?:(Table|Tab\.?|表)\s*([A-Za-z]?\d+(?:\.\d+)?))\b(.*)$",
    flags=re.IGNORECASE,
)
CONTINUED_RE = re.compile(r"\b(cont(?:inued)?\.?)\b|续表", flags=re.IGNORECASE)
TABLE_NOTE_RE = re.compile(r"^\s*(?:Notes?:|注[:：]|[*†‡§]\s*[A-Za-z\u4e00-\u9fff]|[a-d]\s+[A-Za-z])", flags=re.IGNORECASE)


def _clean_cell_text(val: Any) -> str:
    if val is None:
        return ""
    s = re.sub(r"\s+", " ", str(val)).strip()
    s, _ = normalize_ligatures(s)
    return s


def _score_table_grid(grid: List[List[str]]) -> float:
    if not grid or not grid[0]:
        return 0.0
    n_rows = len(grid)
    n_cols = max(len(r) for r in grid)
    if n_rows < 2 or n_cols < 2:
        return 0.1
    total = n_rows * n_cols
    non_empty = sum(1 for r in grid for c in r if c.strip())
    fill_ratio = non_empty / max(1, total)
    if non_empty < 3:
        return 0.05
    return round(0.4 + 0.5 * fill_ratio + min(0.1, 0.01 * n_rows), 3)


def _grid_to_cells_and_outputs(
    grid: List[List[str]],
    cell_specs: Optional[List[Dict[str, Any]]] = None,
    header_row_count: int = 1,
    pages: Optional[List[int]] = None,
    bbox: Optional[Sequence[float]] = None,
) -> Tuple[List[Dict[str, Any]], List[str], str, str, List[Dict[str, Any]]]:
    """Convert normalized grid + optional span specs into cells, col_paths, Markdown, HTML, and records."""
    n_rows = len(grid)
    n_cols = max((len(r) for r in grid), default=0)
    norm_grid = [list(r) + [""] * (n_cols - len(r)) for r in grid]
    h_count = max(1, min(header_row_count, max(1, n_rows - 1)))

    if cell_specs is None:
        cell_specs = []
        default_page = pages[0] if pages else 1
        default_bbox = round_bbox(bbox or [0.0, 0.0, 0.0, 0.0])
        for r_idx in range(n_rows):
            for c_idx in range(n_cols):
                cell_specs.append(
                    {
                        "row": r_idx,
                        "col": c_idx,
                        "rowspan": 1,
                        "colspan": 1,
                        "text": norm_grid[r_idx][c_idx],
                        "page": default_page,
                        "bbox": default_bbox,
                    }
                )

    # Build occupied matrix for HTML generation
    occupied = [[False] * n_cols for _ in range(n_rows)]
    spec_by_pos: Dict[Tuple[int, int], Dict[str, Any]] = {}
    expanded_grid = [[norm_grid[r][c] for c in range(n_cols)] for r in range(n_rows)]

    for sp in sorted(cell_specs, key=lambda x: (x["row"], x["col"])):
        r, c = int(sp["row"]), int(sp["col"])
        rs = max(1, min(int(sp.get("rowspan", 1)), n_rows - r))
        cs = max(1, min(int(sp.get("colspan", 1)), n_cols - c))
        if r >= n_rows or c >= n_cols or occupied[r][c]:
            continue
        sp_copy = dict(sp, rowspan=rs, colspan=cs)
        spec_by_pos[(r, c)] = sp_copy
        for rr in range(r, r + rs):
            for cc in range(c, c + cs):
                occupied[rr][cc] = True
                if not expanded_grid[rr][cc]:
                    expanded_grid[rr][cc] = sp.get("text", "")

    # Build hierarchical col_paths from header rows
    col_paths: List[str] = []
    for c in range(n_cols):
        parts: List[str] = []
        for r in range(h_count):
            val = expanded_grid[r][c].strip()
            if val and (not parts or parts[-1] != val):
                parts.append(val)
        col_paths.append(" / ".join(parts) if parts else f"col_{c + 1}")

    # Markdown (flattened hierarchical headers)
    md_lines: List[str] = []
    if n_cols > 0:
        escaped_headers = [p.replace("|", "\\|") for p in col_paths]
        md_lines.append("| " + " | ".join(escaped_headers) + " |")
        md_lines.append("| " + " | ".join(["---"] * n_cols) + " |")
        for r in range(h_count, n_rows):
            row_vals = [norm_grid[r][c].replace("|", "\\|") for c in range(n_cols)]
            md_lines.append("| " + " | ".join(row_vals) + " |")
    markdown = "\n".join(md_lines)

    # HTML with true rowspan/colspan
    html_parts: List[str] = ["<table>", "<thead>"]
    for r in range(h_count):
        html_parts.append("<tr>")
        for c in range(n_cols):
            sp = spec_by_pos.get((r, c))
            if sp is None:
                continue
            attrs = ""
            if sp["rowspan"] > 1:
                attrs += f' rowspan="{sp["rowspan"]}"'
            if sp["colspan"] > 1:
                attrs += f' colspan="{sp["colspan"]}"'
            html_parts.append(f"<th{attrs}>{html_lib.escape(str(sp['text']))}</th>")
        html_parts.append("</tr>")
    html_parts.append("</thead><tbody>")
    for r in range(h_count, n_rows):
        html_parts.append("<tr>")
        for c in range(n_cols):
            sp = spec_by_pos.get((r, c))
            if sp is None:
                continue
            attrs = ""
            if sp["rowspan"] > 1:
                attrs += f' rowspan="{sp["rowspan"]}"'
            if sp["colspan"] > 1:
                attrs += f' colspan="{sp["colspan"]}"'
            html_parts.append(f"<td{attrs}>{html_lib.escape(str(sp['text']))}</td>")
        html_parts.append("</tr>")
    html_parts.append("</tbody></table>")
    html_str = "".join(html_parts)

    # Records list
    records: List[Dict[str, Any]] = []
    for r in range(h_count, n_rows):
        rec: Dict[str, Any] = {}
        for c in range(n_cols):
            key = col_paths[c]
            if key in rec:
                key = f"{key}_{c + 1}"
            rec[key] = norm_grid[r][c]
        records.append(rec)

    ordered_cells = [spec_by_pos[k] for k in sorted(spec_by_pos.keys())]
    return ordered_cells, col_paths, markdown, html_str, records


def _extract_horizontal_rules(page: Any, min_len: float) -> List[Tuple[float, float, float]]:
    """Extract merged horizontal line rules (x0, y, x1) from PyMuPDF page drawings."""
    raw_segments: List[Tuple[float, float, float]] = []
    for drw in page.get_drawings() or []:
        rect = drw.get("rect")
        if rect is not None:
            w = float(rect.width)
            h = float(rect.height)
            if w >= min_len and h <= 3.5:
                y_mid = (float(rect.y0) + float(rect.y1)) / 2.0
                raw_segments.append((float(rect.x0), y_mid, float(rect.x1)))
        for item in drw.get("items", []):
            if item and item[0] == "l" and len(item) >= 3:
                p1, p2 = item[1], item[2]
                if abs(float(p1.y) - float(p2.y)) <= 2.5:
                    x0 = min(float(p1.x), float(p2.x))
                    x1 = max(float(p1.x), float(p2.x))
                    if x1 - x0 >= min_len:
                        raw_segments.append((x0, (float(p1.y) + float(p2.y)) / 2.0, x1))

    if not raw_segments:
        return []

    raw_segments.sort(key=lambda s: (round(s[1], 1), s[0]))
    merged: List[Tuple[float, float, float]] = []
    for x0, y, x1 in raw_segments:
        if merged and abs(y - merged[-1][1]) <= 2.2:
            px0, py, px1 = merged[-1]
            # Merge overlapping or closely adjacent collinear segments
            if x0 <= px1 + 6.0 and x1 >= px0 - 6.0:
                merged[-1] = (min(px0, x0), (py + y) / 2.0, max(px1, x1))
                continue
        merged.append((x0, y, x1))
    return merged


def _group_words_into_rows(words: List[Tuple[Any, ...]], y_tol: float = 4.0) -> List[List[Tuple[Any, ...]]]:
    if not words:
        return []
    sorted_w = sorted(words, key=lambda w: ((w[1] + w[3]) / 2.0, w[0]))
    rows: List[List[Tuple[Any, ...]]] = []
    row_y: List[float] = []
    for w in sorted_w:
        ym = (w[1] + w[3]) / 2.0
        if rows and abs(ym - row_y[-1]) <= y_tol:
            rows[-1].append(w)
            row_y[-1] = sum((it[1] + it[3]) / 2.0 for it in rows[-1]) / len(rows[-1])
        else:
            rows.append([w])
            row_y.append(ym)
    for r in rows:
        r.sort(key=lambda w: w[0])
    return rows


def _infer_leaf_columns(
    data_rows: List[List[Tuple[Any, ...]]], x_min: float, x_max: float
) -> List[Tuple[float, float]]:
    """Infer column intervals [cx0, cx1] via horizontal occupancy gaps across data rows."""
    width = int(max(10, round(x_max - x_min))) + 4
    occ = [0] * width
    for row in data_rows:
        # Group very close words inside the same row (within 5pt) so spaces inside cells don't split columns
        if not row:
            continue
        phrases: List[Tuple[float, float]] = []
        cur_x0, cur_x1 = float(row[0][0]), float(row[0][2])
        for w in row[1:]:
            wx0, wx1 = float(w[0]), float(w[2])
            if wx0 - cur_x1 <= 5.5:
                cur_x1 = max(cur_x1, wx1)
            else:
                phrases.append((cur_x0, cur_x1))
                cur_x0, cur_x1 = wx0, wx1
        phrases.append((cur_x0, cur_x1))
        for px0, px1 in phrases:
            s = max(0, int(px0 - x_min))
            e = min(width, int(px1 - x_min) + 1)
            for i in range(s, e):
                occ[i] += 1

    # Extract contiguous non-zero intervals
    raw_cols: List[Tuple[float, float]] = []
    in_seg = False
    seg_s = 0
    for i, val in enumerate(occ):
        if val > 0 and not in_seg:
            in_seg = True
            seg_s = i
        elif val == 0 and in_seg:
            in_seg = False
            raw_cols.append((x_min + seg_s, x_min + i))
    if in_seg:
        raw_cols.append((x_min + seg_s, x_min + width))

    # Merge tiny gaps <= 4pt
    merged_cols: List[Tuple[float, float]] = []
    for cx0, cx1 in raw_cols:
        if merged_cols and cx0 - merged_cols[-1][1] <= 4.5:
            merged_cols[-1] = (merged_cols[-1][0], cx1)
        else:
            merged_cols.append((cx0, cx1))
    return merged_cols


def _has_separating_content_or_gap(
    prev_y: float,
    next_y: float,
    gx0: float,
    gx1: float,
    text_lines: Sequence[Tuple[float, float, float, float, str]],
    words: Sequence[Tuple[Any, ...]],
) -> bool:
    """Return True if the vertical region (prev_y, next_y) separates distinct tables."""
    for lx0, ly0, lx1, ly1, ltxt in text_lines:
        if ly0 >= prev_y + 2.0 and ly1 <= next_y - 2.0:
            if max(0.0, min(lx1, gx1 + 20.0) - max(lx0, gx0 - 20.0)) > 0.0:
                if TABLE_CAPTION_RE.match(ltxt) or TABLE_NOTE_RE.match(ltxt):
                    return True

    if next_y - prev_y > 60.0:
        # Check largest vertical blank gap containing no words in [gx0 - 10, gx1 + 10]
        intervals = sorted(
            (float(w[1]), float(w[3]))
            for w in words
            if float(w[0]) >= gx0 - 10.0
            and float(w[2]) <= gx1 + 10.0
            and float(w[3]) > prev_y + 2.0
            and float(w[1]) < next_y - 2.0
        )
        cur_y = prev_y
        for wy0, wy1 in intervals:
            if wy0 - cur_y >= 42.0:
                return True
            cur_y = max(cur_y, wy1)
        if next_y - cur_y >= 42.0:
            return True

    return False


def _detect_three_line_tables_fitz(page: Any, page_num: int) -> List[Dict[str, Any]]:
    """Detect scientific three-line (booktabs) tables with multi-level spanning headers."""
    page_w = float(page.rect.width)
    page_h = float(page.rect.height)
    rules = _extract_horizontal_rules(page, min_len=max(40.0, page_w * 0.14))
    if len(rules) < 2:
        return []

    words = page.get_text("words") or []
    grouped_lines = _group_words_into_rows(words, y_tol=4.5)
    text_lines: List[Tuple[float, float, float, float, str]] = []
    for ln_words in grouped_lines:
        lx0 = min(float(w[0]) for w in ln_words)
        ly0 = min(float(w[1]) for w in ln_words)
        lx1 = max(float(w[2]) for w in ln_words)
        ly1 = max(float(w[3]) for w in ln_words)
        ltxt = " ".join(str(w[4]) for w in ln_words).strip()
        text_lines.append((lx0, ly0, lx1, ly1, ltxt))

    # Separate major rules (wide) and sub-rules (cmidrule)
    # Group rules that share strong horizontal overlap and belong to the same table
    groups: List[List[Tuple[float, float, float]]] = []
    used = [False] * len(rules)
    for i, r in enumerate(rules):
        if used[i]:
            continue
        grp = [r]
        used[i] = True
        for j in range(i + 1, len(rules)):
            if used[j]:
                continue
            sx0, sy, sx1 = rules[j]
            prev_y = grp[-1][1]
            if sy - prev_y > page_h * 0.55:
                break
            gx0 = min(g[0] for g in grp)
            gx1 = max(g[2] for g in grp)
            if _has_separating_content_or_gap(prev_y, sy, gx0, gx1, text_lines, words):
                break
            # Overlap against current group's horizontal span
            ov = max(0.0, min(gx1, sx1) - max(gx0, sx0))
            min_w = min(gx1 - gx0, sx1 - sx0)
            if min_w > 0 and ov / min_w >= 0.70:
                grp.append(rules[j])
                used[j] = True
        if len(grp) >= 2:
            groups.append(grp)

    results: List[Dict[str, Any]] = []

    for grp in groups:
        grp.sort(key=lambda item: item[1])
        max_w = max(g[2] - g[0] for g in grp)
        major_rules = [g for g in grp if (g[2] - g[0]) >= max_w * 0.78]
        if len(major_rules) < 2:
            continue

        top_rule = major_rules[0]
        bot_rule = major_rules[-1]
        if bot_rule[1] - top_rule[1] < 24.0:
            continue

        tx0 = min(g[0] for g in major_rules)
        tx1 = max(g[2] for g in major_rules)
        top_y = top_rule[1]
        bot_y = bot_rule[1]

        # Midrule separates header from body
        mid_candidates = [g for g in major_rules[1:-1] if g[1] - top_y >= 10.0 and bot_y - g[1] >= 12.0]
        mid_y = mid_candidates[0][1] if mid_candidates else None
        cmidrules = [
            g
            for g in grp
            if g not in major_rules and top_y < g[1] < (mid_y if mid_y is not None else bot_y)
        ]

        table_words = [
            w
            for w in words
            if w[0] >= tx0 - 6.0 and w[2] <= tx1 + 6.0 and w[1] >= top_y - 3.0 and w[3] <= bot_y + 3.0
        ]
        if len(table_words) < 4:
            continue

        rows = _group_words_into_rows(table_words, y_tol=4.5)
        if len(rows) < 2:
            continue

        if mid_y is not None:
            header_rows = [r for r in rows if ((r[0][1] + r[0][3]) / 2.0) < mid_y]
            data_rows = [r for r in rows if ((r[0][1] + r[0][3]) / 2.0) >= mid_y]
        else:
            header_rows = rows[:1]
            data_rows = rows[1:]

        if not header_rows or not data_rows:
            continue

        leaf_cols = _infer_leaf_columns(data_rows, tx0 - 4.0, tx1 + 4.0)
        if len(leaf_cols) < 2:
            continue

        n_cols = len(leaf_cols)
        h_count = len(header_rows)
        n_rows = h_count + len(data_rows)
        grid: List[List[str]] = [[""] * n_cols for _ in range(n_rows)]
        cell_specs: List[Dict[str, Any]] = []

        # Map header rows with colspan detection
        header_occupied = [[False] * n_cols for _ in range(h_count)]
        for r_idx, h_words in enumerate(header_rows):
            # Cluster words into header phrases (allow wider intra-phrase gap up to 7pt)
            phrases: List[List[Tuple[Any, ...]]] = []
            for w in h_words:
                if phrases and float(w[0]) - float(phrases[-1][-1][2]) <= 7.0:
                    phrases[-1].append(w)
                else:
                    phrases.append([w])

            for ph in phrases:
                px0 = min(float(w[0]) for w in ph)
                py0 = min(float(w[1]) for w in ph)
                px1 = max(float(w[2]) for w in ph)
                py1 = max(float(w[3]) for w in ph)
                ptxt = _clean_cell_text(" ".join(str(w[4]) for w in ph))
                if not ptxt:
                    continue

                # Expand spanning x-interval if a cmidrule sits directly underneath this upper header phrase
                span_x0, span_x1 = px0, px1
                if r_idx < h_count - 1 and cmidrules:
                    for cm_x0, cm_y, cm_x1 in cmidrules:
                        if -3.0 <= (cm_y - py1) <= 20.0 and cm_x0 - 8.0 <= px0 and px1 <= cm_x1 + 8.0:
                            span_x0, span_x1 = cm_x0, cm_x1
                            break

                # Determine which leaf columns this header phrase spans
                covered_cols = []
                for c_idx, (cx0, cx1) in enumerate(leaf_cols):
                    ov = max(0.0, min(span_x1, cx1) - max(span_x0, cx0))
                    c_w = max(1.0, cx1 - cx0)
                    p_mid = (span_x0 + span_x1) / 2.0
                    if ov / c_w >= 0.25 or (cx0 - 3.0 <= p_mid <= cx1 + 3.0):
                        covered_cols.append(c_idx)
                if not covered_cols:
                    # Fallback nearest column center
                    p_mid = (px0 + px1) / 2.0
                    nearest = min(range(n_cols), key=lambda ci: abs((leaf_cols[ci][0] + leaf_cols[ci][1]) / 2.0 - p_mid))
                    covered_cols = [nearest]

                c_start = min(covered_cols)
                c_end = max(covered_cols)
                colspan = c_end - c_start + 1

                # Check if below rows in header are empty for all covered cols (vertical rowspan in header)
                rowspan = 1
                if r_idx + 1 < h_count:
                    below_has_words = False
                    for rr in range(r_idx + 1, h_count):
                        for w in header_rows[rr]:
                            wx0, wx1 = float(w[0]), float(w[2])
                            if max(0.0, min(wx1, leaf_cols[c_end][1]) - max(wx0, leaf_cols[c_start][0])) > 1.0:
                                below_has_words = True
                                break
                        if below_has_words:
                            break
                    if not below_has_words:
                        rowspan = h_count - r_idx

                for rr in range(r_idx, r_idx + rowspan):
                    for cc in range(c_start, c_start + colspan):
                        header_occupied[rr][cc] = True
                        if not grid[rr][cc]:
                            grid[rr][cc] = ptxt

                cell_specs.append(
                    {
                        "row": r_idx,
                        "col": c_start,
                        "rowspan": rowspan,
                        "colspan": colspan,
                        "text": ptxt,
                        "page": page_num,
                        "bbox": round_bbox([px0, py0, px1, py1]),
                    }
                )

        # Fill any uncovered header slots
        for r_idx in range(h_count):
            for c_idx in range(n_cols):
                if not header_occupied[r_idx][c_idx]:
                    cell_specs.append(
                        {
                            "row": r_idx,
                            "col": c_idx,
                            "rowspan": 1,
                            "colspan": 1,
                            "text": "",
                            "page": page_num,
                            "bbox": round_bbox([leaf_cols[c_idx][0], top_y, leaf_cols[c_idx][1], mid_y or bot_y]),
                        }
                    )

        # Map data rows into leaf columns
        for d_idx, d_words in enumerate(data_rows):
            r_idx = h_count + d_idx
            col_words: List[List[Tuple[Any, ...]]] = [[] for _ in range(n_cols)]
            for w in d_words:
                w_mid = (float(w[0]) + float(w[2])) / 2.0
                best_c = min(
                    range(n_cols),
                    key=lambda ci: (
                        0.0
                        if leaf_cols[ci][0] - 2.0 <= w_mid <= leaf_cols[ci][1] + 2.0
                        else abs((leaf_cols[ci][0] + leaf_cols[ci][1]) / 2.0 - w_mid)
                    ),
                )
                col_words[best_c].append(w)

            row_y0 = min(float(w[1]) for w in d_words)
            row_y1 = max(float(w[3]) for w in d_words)
            for c_idx in range(n_cols):
                cw = col_words[c_idx]
                txt = _clean_cell_text(" ".join(str(w[4]) for w in cw))
                grid[r_idx][c_idx] = txt
                cb = (
                    round_bbox(
                        [
                            min(float(w[0]) for w in cw),
                            min(float(w[1]) for w in cw),
                            max(float(w[2]) for w in cw),
                            max(float(w[3]) for w in cw),
                        ]
                    )
                    if cw
                    else round_bbox([leaf_cols[c_idx][0], row_y0, leaf_cols[c_idx][1], row_y1])
                )
                cell_specs.append(
                    {
                        "row": r_idx,
                        "col": c_idx,
                        "rowspan": 1,
                        "colspan": 1,
                        "text": txt,
                        "page": page_num,
                        "bbox": cb,
                    }
                )

        score = _score_table_grid(grid)
        if mid_y is not None:
            score = min(0.99, round(score + 0.08, 3))
        t_bbox = round_bbox([tx0, top_y, tx1, bot_y])
        results.append(
            {
                "page": page_num,
                "bbox": t_bbox,
                "grid": grid,
                "cell_specs": cell_specs,
                "header_row_count": h_count,
                "col_centers": [round((cx0 + cx1) / 2.0, 2) for cx0, cx1 in leaf_cols],
                "extraction_method": "three_line_booktabs",
                "confidence": score,
            }
        )

    return results


def _extract_fitz_tables(page: Any, page_num: int) -> List[Dict[str, Any]]:
    if not hasattr(page, "find_tables"):
        return []
    out: List[Dict[str, Any]] = []
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            finder = page.find_tables()
        for tab in getattr(finder, "tables", []):
            raw_grid = tab.extract() or []
            grid = [[_clean_cell_text(c) for c in row] for row in raw_grid if any(_clean_cell_text(c) for c in row)]
            score = _score_table_grid(grid)
            if score < 0.35:
                continue
            tb = round_bbox(tab.bbox)
            n_cols = max(len(r) for r in grid)
            col_w = (tb[2] - tb[0]) / max(1, n_cols)
            col_centers = [round(tb[0] + (i + 0.5) * col_w, 2) for i in range(n_cols)]
            out.append(
                {
                    "page": page_num,
                    "bbox": tb,
                    "grid": grid,
                    "cell_specs": None,
                    "header_row_count": 1,
                    "col_centers": col_centers,
                    "extraction_method": "pymupdf_find_tables",
                    "confidence": score,
                }
            )
    except Exception:
        return []
    return out


def _extract_pdfplumber_tables(plumber_page: Any, page_num: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        tables = plumber_page.find_tables(
            table_settings={"vertical_strategy": "lines", "horizontal_strategy": "lines"}
        )
        for tab in tables:
            raw_grid = tab.extract() or []
            grid = [[_clean_cell_text(c) for c in row] for row in raw_grid if any(_clean_cell_text(c) for c in row)]
            score = _score_table_grid(grid)
            if score < 0.35:
                continue
            tb = round_bbox(tab.bbox)
            n_cols = max(len(r) for r in grid)
            col_w = (tb[2] - tb[0]) / max(1, n_cols)
            col_centers = [round(tb[0] + (i + 0.5) * col_w, 2) for i in range(n_cols)]
            out.append(
                {
                    "page": page_num,
                    "bbox": tb,
                    "grid": grid,
                    "cell_specs": None,
                    "header_row_count": 1,
                    "col_centers": col_centers,
                    "extraction_method": "pdfplumber_lattice",
                    "confidence": round(min(0.98, score + 0.04), 3),
                }
            )
    except Exception:
        return []
    return out


def _deduplicate_page_tables(cands: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # Prefer three-line booktabs / lattice with higher confidence when overlapping
    method_bonus = {"three_line_booktabs": 0.05, "pdfplumber_lattice": 0.03, "pymupdf_find_tables": 0.0}
    ranked = sorted(
        cands,
        key=lambda t: t["confidence"] + method_bonus.get(t["extraction_method"], 0.0),
        reverse=True,
    )
    kept: List[Dict[str, Any]] = []
    for cand in ranked:
        overlap = False
        for k in kept:
            if (
                bbox_iou(cand["bbox"], k["bbox"]) >= 0.45
                or bbox_overlap_ratio(cand["bbox"], k["bbox"]) >= 0.65
                or bbox_overlap_ratio(k["bbox"], cand["bbox"]) >= 0.65
            ):
                overlap = True
                break
        if not overlap:
            kept.append(cand)
    kept.sort(key=lambda t: (t["bbox"][1], t["bbox"][0]))
    return kept


def _extract_page_text_blocks(page: Any) -> List[Dict[str, Any]]:
    pdict = page.get_text("dict")
    blks: List[Dict[str, Any]] = []
    for blk in pdict.get("blocks", []):
        if blk.get("type", 0) != 0:
            continue
        lines_txt = []
        for ln in blk.get("lines", []):
            lt = "".join(sp.get("text", "") for sp in ln.get("spans", [])).strip()
            if lt:
                lines_txt.append(lt)
        txt = " ".join(lines_txt).strip()
        if txt:
            txt, _ = normalize_ligatures(txt)
            blks.append({"bbox": round_bbox(blk["bbox"]), "text": txt})
    return blks


def _bind_caption_and_notes(
    table_bbox: Sequence[float],
    page_blocks: List[Dict[str, Any]],
    all_table_bboxes: List[Sequence[float]],
) -> Tuple[Optional[str], Optional[str], Optional[str], List[str], List[float]]:
    """Find caption (above or below) and trailing notes for a table bbox."""
    tx0, ty0, tx1, ty1 = table_bbox
    caption_text: Optional[str] = None
    label: Optional[str] = None
    table_num: Optional[str] = None
    caption_bbox: Optional[List[float]] = None

    best_dist = 9999.0
    for blk in page_blocks:
        # Skip blocks inside any table
        if any(bbox_overlap_ratio(blk["bbox"], tb) >= 0.55 for tb in all_table_bboxes):
            continue
        m = TABLE_CAPTION_RE.match(blk["text"])
        if not m:
            continue
        bx0, by0, bx1, by1 = blk["bbox"]
        # Check horizontal overlap or reasonable proximity
        h_ov = max(0.0, min(bx1, tx1 + 40.0) - max(bx0, tx0 - 40.0))
        if h_ov <= 0.0:
            continue
        if 0.0 <= ty0 - by1 <= 65.0:
            dist = ty0 - by1
        elif 0.0 <= by0 - ty1 <= 45.0:
            dist = by0 - ty1 + 5.0
        else:
            continue

        if dist < best_dist:
            best_dist = dist
            caption_text = blk["text"]
            label = f"{m.group(1)} {m.group(2)}".strip()
            table_num = m.group(2)
            caption_bbox = blk["bbox"]

    notes: List[str] = []
    for blk in page_blocks:
        if caption_bbox is not None and blk["bbox"] == caption_bbox:
            continue
        if any(bbox_overlap_ratio(blk["bbox"], tb) >= 0.55 for tb in all_table_bboxes):
            continue
        bx0, by0, bx1, by1 = blk["bbox"]
        if 0.0 <= by0 - ty1 <= 48.0 and max(0.0, min(bx1, tx1 + 30.0) - max(bx0, tx0 - 30.0)) > 0:
            if TABLE_NOTE_RE.match(blk["text"]):
                notes.append(blk["text"])

    return label, table_num, caption_text, notes, caption_bbox or []


def _can_merge_cross_page(
    prev_tab: Dict[str, Any], cur_tab: Dict[str, Any], cur_page_h: float
) -> bool:
    if cur_tab["page"] != prev_tab["pages"][-1] + 1:
        return False

    cur_cap = cur_tab.get("caption") or ""
    prev_num = prev_tab.get("table_num")
    cur_num = cur_tab.get("table_num")

    # Explicit continued caption match
    if cur_cap and CONTINUED_RE.search(cur_cap):
        if prev_num and cur_num and prev_num.lower() == cur_num.lower():
            return True
        if cur_num is None:
            return True

    # Implicit continuation near top of next page without a different Table M caption
    if cur_tab["bbox"][1] <= cur_page_h * 0.30 and (cur_num is None or cur_num == prev_num):
        prev_cols = len(prev_tab["grid"][0]) if prev_tab.get("grid") else 0
        cur_cols = len(cur_tab["grid"][0]) if cur_tab.get("grid") else 0
        if prev_cols == cur_cols and prev_cols >= 2:
            # Either column x-centers align or first row repeats previous header
            prev_header = [c.lower() for c in prev_tab["grid"][0]]
            cur_first = [c.lower() for c in cur_tab["grid"][0]]
            if prev_header == cur_first:
                return True
            pc = prev_tab.get("col_centers", [])
            cc = cur_tab.get("col_centers", [])
            if len(pc) == len(cc) and all(abs(a - b) <= 24.0 for a, b in zip(pc, cc)):
                return True

    return False


def _merge_tables_cross_page(prev_tab: Dict[str, Any], cur_tab: Dict[str, Any]) -> None:
    prev_grid = prev_tab["grid"]
    cur_grid = cur_tab["grid"]
    if not cur_grid:
        return

    h_count = prev_tab.get("header_row_count", 1)
    # Detect repeated header rows on continuation page
    skip_rows = 0
    if len(cur_grid) > h_count:
        matches = True
        for r in range(h_count):
            if [c.strip().lower() for c in cur_grid[r]] != [c.strip().lower() for c in prev_grid[r]]:
                matches = False
                break
        if matches:
            skip_rows = h_count
    if skip_rows == 0 and len(cur_grid) > 1:
        if [c.strip().lower() for c in cur_grid[0]] == [c.strip().lower() for c in prev_grid[0]]:
            skip_rows = 1

    base_row_offset = len(prev_grid)
    appended_rows = cur_grid[skip_rows:]
    prev_tab["grid"].extend(appended_rows)
    prev_tab["pages"].append(cur_tab["page"])
    prev_tab["bboxes_by_page"][str(cur_tab["page"])] = cur_tab["bbox"]
    prev_tab["is_cross_page_merged"] = True
    if cur_tab.get("notes"):
        prev_tab["notes"].extend(cur_tab["notes"])

    # Append cell specs with updated row indices
    if cur_tab.get("cell_specs"):
        for sp in cur_tab["cell_specs"]:
            if sp["row"] < skip_rows:
                continue
            new_sp = dict(sp)
            new_sp["row"] = base_row_offset + (sp["row"] - skip_rows)
            prev_tab["cell_specs"].append(new_sp)
    else:
        for r_off, row in enumerate(appended_rows):
            for c_idx, val in enumerate(row):
                prev_tab["cell_specs"].append(
                    {
                        "row": base_row_offset + r_off,
                        "col": c_idx,
                        "rowspan": 1,
                        "colspan": 1,
                        "text": val,
                        "page": cur_tab["page"],
                        "bbox": cur_tab["bbox"],
                    }
                )


def extract_tables(
    pdf_path: str | Path,
    diagnostics: Dict[str, Any],
    crops_dir: Optional[Path] = None,
    ocr_fn: Optional[Callable[[Any, int, Sequence[float], Optional[Path]], Dict[str, Any]]] = None,
    max_pages: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Extract structured tables, merge cross-page continuations, and detect image-based tables."""
    if diagnostics.get("encrypted") or diagnostics.get("corrupt") or fitz is None:
        return []

    path = Path(pdf_path)
    if not path.exists():
        return []

    doc = fitz.open(str(path))
    plumber_doc = pdfplumber.open(str(path)) if pdfplumber is not None else None

    try:
        limit = doc.page_count if max_pages is None else min(doc.page_count, max_pages)
        per_page_tables: List[Dict[str, Any]] = []
        page_heights: Dict[int, float] = {}
        page_blocks_cache: Dict[int, List[Dict[str, Any]]] = {}

        for idx in range(limit):
            page_num = idx + 1
            page = doc[idx]
            page_heights[page_num] = float(page.rect.height)
            page_blocks = _extract_page_text_blocks(page)
            page_blocks_cache[page_num] = page_blocks

            cands: List[Dict[str, Any]] = []
            cands.extend(_detect_three_line_tables_fitz(page, page_num))
            cands.extend(_extract_fitz_tables(page, page_num))
            if plumber_doc is not None and idx < len(plumber_doc.pages):
                cands.extend(_extract_pdfplumber_tables(plumber_doc.pages[idx], page_num))

            deduped = _deduplicate_page_tables(cands)
            all_bboxes = [t["bbox"] for t in deduped]
            matched_caption_bboxes = set()

            for tab in deduped:
                label, tnum, cap, notes, cap_bbox = _bind_caption_and_notes(
                    tab["bbox"], page_blocks, all_bboxes
                )
                if cap_bbox:
                    matched_caption_bboxes.add(tuple(cap_bbox))
                tab["label"] = label
                tab["table_num"] = tnum
                tab["caption"] = cap
                tab["notes"] = notes
                tab["pages"] = [page_num]
                tab["bboxes_by_page"] = {str(page_num): tab["bbox"]}
                tab["is_cross_page_merged"] = False
                tab["is_image_table"] = False
                if tab["cell_specs"] is None:
                    # Build default cell_specs for non-three-line tables
                    specs = []
                    for r_i, row in enumerate(tab["grid"]):
                        for c_i, val in enumerate(row):
                            specs.append(
                                {
                                    "row": r_i,
                                    "col": c_i,
                                    "rowspan": 1,
                                    "colspan": 1,
                                    "text": val,
                                    "page": page_num,
                                    "bbox": tab["bbox"],
                                }
                            )
                    tab["cell_specs"] = specs
                per_page_tables.append(tab)

            # Detect image-based tables: Table caption near raster image not matched to vector table
            img_infos = []
            try:
                img_infos = page.get_image_info(xrefs=False)
            except Exception:
                img_infos = []

            if img_infos:
                for blk in page_blocks:
                    if tuple(blk["bbox"]) in matched_caption_bboxes:
                        continue
                    m = TABLE_CAPTION_RE.match(blk["text"])
                    if not m:
                        continue
                    bx0, by0, bx1, by1 = blk["bbox"]
                    # Check if a raster image lies within 280pt below or above this caption
                    matched_img_bbox: Optional[List[float]] = None
                    for info in img_infos:
                        ib = info.get("bbox")
                        if not ib:
                            continue
                        ix0, iy0, ix1, iy1 = [float(v) for v in ib[:4]]
                        if (ix1 - ix0) < 60.0 or (iy1 - iy0) < 30.0:
                            continue
                        if any(bbox_overlap_ratio(ib, tb) >= 0.45 for tb in all_bboxes):
                            continue
                        h_ov = max(0.0, min(ix1, bx1 + 80.0) - max(ix0, bx0 - 80.0))
                        if h_ov <= 0.0:
                            continue
                        if (0.0 <= iy0 - by1 <= 260.0) or (0.0 <= by0 - iy1 <= 260.0):
                            matched_img_bbox = round_bbox(ib)
                            break

                    if matched_img_bbox is not None:
                        crop_path = None
                        ocr_res: Optional[Dict[str, Any]] = None
                        if crops_dir is not None or ocr_fn is not None:
                            pix = page.get_pixmap(dpi=300, clip=fitz.Rect(*matched_img_bbox))
                            if crops_dir is not None:
                                crops_dir.mkdir(parents=True, exist_ok=True)
                                crop_file = crops_dir / f"image_table_p{page_num}_n{len(per_page_tables) + 1}.png"
                                pix.save(str(crop_file))
                                crop_path = str(crop_file)
                            else:
                                crop_file = None
                            if ocr_fn is not None and Image is not None:
                                pil_img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                                ocr_res = ocr_fn(pil_img, page_num, matched_img_bbox, crop_file)

                        per_page_tables.append(
                            {
                                "page": page_num,
                                "pages": [page_num],
                                "bbox": matched_img_bbox,
                                "bboxes_by_page": {str(page_num): matched_img_bbox},
                                "label": f"{m.group(1)} {m.group(2)}".strip(),
                                "table_num": m.group(2),
                                "caption": blk["text"],
                                "notes": [],
                                "grid": [],
                                "cell_specs": [],
                                "header_row_count": 0,
                                "col_centers": [],
                                "extraction_method": "image_table_detected",
                                "confidence": 0.45 if (ocr_res and ocr_res.get("text")) else 0.0,
                                "is_cross_page_merged": False,
                                "is_image_table": True,
                                "status": "image_table_requires_ocr_or_vision",
                                "crop_path": crop_path,
                                "ocr_text": ocr_res.get("text", "") if ocr_res else "",
                                "ocr_status": ocr_res.get("ocr_status") if ocr_res else "not_run",
                            }
                        )

        # Merge cross-page tables
        merged_tables: List[Dict[str, Any]] = []
        for tab in per_page_tables:
            if (
                not tab.get("is_image_table")
                and merged_tables
                and not merged_tables[-1].get("is_image_table")
                and _can_merge_cross_page(merged_tables[-1], tab, page_heights.get(tab["page"], 792.0))
            ):
                _merge_tables_cross_page(merged_tables[-1], tab)
            else:
                merged_tables.append(tab)

        # Final formatting
        final_tables: List[Dict[str, Any]] = []
        for idx, tab in enumerate(merged_tables, start=1):
            table_id = f"tab_{idx}"
            if tab.get("is_image_table"):
                final_tables.append(
                    {
                        "table_id": table_id,
                        "label": tab.get("label") or f"Table {idx}",
                        "caption": tab.get("caption"),
                        "notes": tab.get("notes", []),
                        "pages": tab["pages"],
                        "bbox": tab["bbox"],
                        "bboxes_by_page": tab["bboxes_by_page"],
                        "n_rows": 0,
                        "n_cols": 0,
                        "header_row_count": 0,
                        "col_paths": [],
                        "cells": [],
                        "markdown": "",
                        "html": "",
                        "records": [],
                        "extraction_method": tab["extraction_method"],
                        "confidence": tab["confidence"],
                        "is_cross_page_merged": False,
                        "is_image_table": True,
                        "status": tab["status"],
                        "crop_path": tab.get("crop_path"),
                        "ocr_text": tab.get("ocr_text", ""),
                        "ocr_status": tab.get("ocr_status", "not_run"),
                    }
                )
                continue

            cells, col_paths, md, html_str, records = _grid_to_cells_and_outputs(
                tab["grid"],
                cell_specs=tab["cell_specs"],
                header_row_count=tab.get("header_row_count", 1),
                pages=tab["pages"],
                bbox=tab["bbox"],
            )
            final_tables.append(
                {
                    "table_id": table_id,
                    "label": tab.get("label") or f"Table {idx}",
                    "caption": tab.get("caption"),
                    "notes": tab.get("notes", []),
                    "pages": tab["pages"],
                    "bbox": tab["bbox"],
                    "bboxes_by_page": tab["bboxes_by_page"],
                    "n_rows": len(tab["grid"]),
                    "n_cols": len(col_paths),
                    "header_row_count": tab.get("header_row_count", 1),
                    "col_paths": col_paths,
                    "cells": cells,
                    "markdown": md,
                    "html": html_str,
                    "records": records,
                    "extraction_method": tab["extraction_method"],
                    "confidence": tab["confidence"],
                    "is_cross_page_merged": bool(tab.get("is_cross_page_merged")),
                    "is_image_table": False,
                    "status": "extracted",
                    "crop_path": None,
                }
            )

        return final_tables
    finally:
        doc.close()
        if plumber_doc is not None:
            plumber_doc.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract scientific tables from a PDF.")
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    from diagnose_pdf import diagnose_pdf

    diag = diagnose_pdf(args.pdf)
    tabs = extract_tables(args.pdf, diag)
    write_json_atomic(args.out, tabs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
