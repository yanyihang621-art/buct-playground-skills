#!/usr/bin/env python3
"""Apply glossary-locked bilingual translation and render JSON / Markdown / HTML views."""

from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from pdf_utils import write_json_atomic, write_text_lf
from segment_and_protect import sentence_spans

VERBATIM_ROLES = {"authors", "affiliation", "reference", "equation"}

HAN_RE = re.compile(r"[\u3400-\u9fff]")
LATIN_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]*")
SENTENCE_BREAK = re.compile(r"(?<=[.!?;])\s+|[。！？；]")


def verbatim_allowed(seg: Dict[str, Any], glossary_entries: List[Dict[str, Any]]) -> bool:
    """Identify source-only material such as references, bare formulae or locked identifiers."""
    if seg.get("role") in VERBATIM_ROLES:
        return True
    text = seg.get("masked_text", seg.get("source_text", ""))
    text = re.sub(r"__(?:EQ|CIT|REF|NUM)_\d+__", "", text)
    for entry in glossary_entries:
        if entry.get("status") == "keep_verbatim":
            text = re.sub(rf"(?<![\w-]){re.escape(entry['source_term'])}(?![\w-])", "", text)
    if any(e.get("status") == "locked" for e in _find_locked_terms_in_source(text, glossary_entries)):
        return False
    if not LATIN_WORD_RE.search(text):
        return True
    single = text.strip(" .()[]")
    # A lone acronym, chemical identifier or algebraic variable may remain source-only.
    return bool(re.fullmatch(r"[A-Z]{1,5}\d*|(?:[A-Z][a-z]?\d*){2,}|(?:[A-Z][a-z]?\d*)+(?:[-/](?:[A-Z][a-z]?\d*)+)+|[a-zA-Z](?:[_^]\w+)?", single))


def copied_prose(seg: Dict[str, Any], target: str, glossary_entries: List[Dict[str, Any]]) -> bool:
    if verbatim_allowed(seg, glossary_entries):
        return False
    if not HAN_RE.search(target):
        return True
    normalized = " ".join(target.casefold().split())
    for part in SENTENCE_BREAK.split(seg["source_text"]):
        sentence = part.strip(" .!?;").casefold()
        if len(LATIN_WORD_RE.findall(sentence)) >= 4 and " ".join(sentence.split()) in normalized:
            return True
    return False


def restore_protected_tokens(text: str, protected_tokens: Dict[str, str]) -> str:
    out = text
    for key, val in protected_tokens.items():
        out = out.replace(key, val)
    return out


def align_sentences(seg, target, authored=None):
    """Align prose with source offsets; an authored clause may span several Chinese sentences."""
    if seg.get("role") not in {"body", "abstract", "caption", "footnote", "table_cell", "table_note"}:
        return []
    src = seg["source_text"]
    spans = sentence_spans(src)
    if not spans or not target:
        return []
    if authored is not None:
        parts = authored
    elif len(spans) == 1:
        parts = [target]
    else:
        parts = [s.strip() for s in re.findall(r"[^。！？]+[。！？]*", target) if s.strip()]
    if len(parts) != len(spans) or any(not part.strip() for part in parts):
        return []
    return [{"sentence_id": f"{seg['segment_id']}.s{i}", "source_start": a, "source_end": b,
             "source_text": src[a:b], "target_text": zh}
            for i, ((a, b), zh) in enumerate(zip(spans, parts), 1)]


def _find_locked_terms_in_source(source_text: str, glossary_entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    matched: List[Dict[str, Any]] = []
    for entry in glossary_entries:
        variants = [entry["source_term"]] + list(entry.get("aliases", []))
        for v in variants:
            pat = re.compile(rf"(?<![A-Za-z0-9\-]){re.escape(v)}(?![A-Za-z0-9\-])", re.IGNORECASE)
            if pat.search(source_text):
                matched.append(entry)
                break
    return matched


def _location(seg: Dict[str, Any]) -> str:
    spans = seg.get("source_spans") or [seg.get("source_locator") or {}]
    labels = []
    for loc in spans:
        if "pdf_page" in loc:
            detail = f"PDF p.{loc['pdf_page']}"
        elif "line_start" in loc:
            detail = f"lines {loc['line_start']}–{loc.get('line_end', loc['line_start'])}"
        elif "docx_paragraph" in loc:
            detail = f"paragraph {loc['docx_paragraph']}"
        elif "json_pointer" in loc:
            detail = str(loc["json_pointer"])
        else:
            detail = str(seg.get("block_id", ""))
        if "field" in loc:
            detail += f" {loc['field']} {loc.get('index', '')}"
        if "row" in loc and "column" in loc:
            detail += f" (r{loc['row']}, c{loc['column']})"
        labels.append(" ".join(filter(None, [str(loc.get("source", "")), detail])))
    return " → ".join(labels)


def _table_markdown(table):
    columns = table.get("col_paths_zh", [])
    if not columns:
        return []
    grid = [columns, ["---"] * len(columns), *table.get("rows_zh", [])]
    return ["| " + " | ".join(str(v).replace("|", r"\|").replace("\n", " ") for v in row) + " |" for row in grid]


def _render_bilingual_markdown(doc: Dict[str, Any]) -> str:
    lines: List[str] = []
    title_zh = doc.get("title_zh") or doc.get("title_en") or "文献双语对照译文"
    lines.append(f"# {title_zh}")
    if doc.get("title_en") and doc.get("title_zh") and doc["title_en"] != doc["title_zh"]:
        lines.append(f"**原文标题 / Source Title:** {doc['title_en']}")
    lines.append(f"**翻译状态 / Status:** `{doc['status']}` | **段落数 / Segments:** {len(doc['segments'])}")
    lines.append("")

    if doc.get("uncertainties"):
        lines.append("## 待核验与异常提示 (Uncertainties & Review Notes)")
        for u in doc["uncertainties"]:
            lines.append(f"- **[{u.get('severity', 'warning')}] {u.get('code', 'uncertainty')}** {u.get('segment_id', '')} {u.get('message') or u.get('detail', '')}")
        lines.append("")

    entries = doc.get("glossary", {}).get("entries", [])
    if entries:
        lines.append("## 锁定术语表 (Locked Terminology Glossary)")
        lines.append("| Term ID | 原文术语 (EN) | 锁定译名 (ZH) | 状态 | 首次出现 |")
        lines.append("|---|---|---|---|---|")
        for e in entries:
            fs = e.get("first_seen") or {}
            loc = str(fs.get('block_id', ''))
            lines.append(f"| `{e['term_id']}` | {e['source_term']} | {e['target_zh']} | `{e['status']}` | {loc} |")
        lines.append("")

    lines.append("## 双语段落对照 (Bilingual Aligned Segments)")
    lines.append("")
    for seg in doc.get("segments", []):
        anchor = f"[`{seg['segment_id']}` | {_location(seg)} | `{seg['role']}` | `{seg['status']}`]"
        lines.append(f"### {anchor}")
        lines.append(f"- **EN:** {seg['source_text']}")
        lines.append(f"- **ZH:** {seg['target_text']}")
        if seg.get("rationale"):
            lines.append(f"- *说明:* {seg['rationale']}")
        lines.append("")

    if doc.get("tables_bilingual"):
        lines.append("## 表格双语对照")
        for t in doc["tables_bilingual"]:
            lines.append(f"### {t['table_id']} | Status: `{t['status']}`")
            lines.append(f"- **EN Caption:** {t['caption_en']}")
            lines.append(f"- **ZH Caption:** {t['caption_zh']}")
            if t.get("col_paths_en"):
                lines.append(f"- **Columns EN -> ZH:** `{' | '.join(t['col_paths_en'])}` -> `{' | '.join(t['col_paths_zh'])}`")
            lines.append("")

            if t.get("col_paths_zh") and t.get("rows_zh"):
                lines.extend(_table_markdown(t))
                lines.append("")
    return "\n".join(lines).strip() + "\n"


def _render_bilingual_html(doc: Dict[str, Any]) -> str:
    rows: List[str] = []
    for seg in doc.get("segments", []):
        rows.append(
            "<tr>"
            f"<td><code>{html_lib.escape(seg['segment_id'])}</code><br><small>{html_lib.escape(_location(seg))} · {html_lib.escape(seg['role'])}</small></td>"
            f"<td>{html_lib.escape(seg['source_text'])}</td>"
            f"<td>{html_lib.escape(seg['target_text'])}</td>"
            "</tr>"
        )
    title = html_lib.escape(str(doc.get("title_zh") or doc.get("title_en") or "Bilingual Translation"))
    return (
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
        f"<title>{title}</title>"
        "<style>body{font-family:system-ui,-apple-system,sans-serif;margin:24px;color:#1f2937;}"
        "table{width:100%;border-collapse:collapse;margin-top:16px;}"
        "th,td{border:1px solid #d1d5db;padding:10px;vertical-align:top;text-align:left;}"
        "th{background:#f3f4f6;}td:first-child{width:170px;background:#fafafa;}</style>"
        f"</head><body><h1>{title}</h1>"
        f"<p>Status: <code>{html_lib.escape(doc['status'])}</code> | Segments: {len(doc['segments'])}</p>"
        "<table><thead><tr><th>Anchor</th><th>Source (EN)</th><th>Translation (ZH)</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></body></html>\n"
    )


def _render_zh_only(doc: Dict[str, Any]) -> str:
    lines: List[str] = []
    title_zh = doc.get("title_zh") or doc.get("title_en") or "文献中文译文"
    lines.append(f"# {title_zh}")
    lines.append("")
    first_title = True
    tables = {t["table_id"]: t for t in doc.get("tables_bilingual", [])}
    rendered_tables = set()
    for seg in doc.get("segments", []):
        role = seg.get("role", "body")
        tid = seg.get("table_id")
        if tid in tables:
            if tid not in rendered_tables:
                tab = tables[tid]
                caption_id = tab.get("translation_ids", {}).get("caption") or ""
                if caption_id.startswith(tid + "_") and tab.get("caption_zh"):
                    lines.extend([tab["caption_zh"], ""])
                lines.extend([*_table_markdown(tab), ""])
                lines.extend(note + "\n" for note in tab.get("notes_zh", []) if note)
                rendered_tables.add(tid)
            continue
        txt = seg.get("target_text") or f"〔待译：{seg['segment_id']}〕"
        if role == "title" and first_title:
            first_title = False
            continue
        if role in ("heading", "title"):
            lines.append(f"## {txt}")
        elif role == "caption":
            lines.append(f"**{txt}**")
        elif role == "footnote":
            lines.append(f"> {txt}")
        else:
            lines.append(txt)
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def apply_translations(
    segments_data: Dict[str, Any],
    glossary_data: Dict[str, Any],
    translations_override: Optional[Dict[str, Any]] = None,
    seed_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Assemble bilingual_doc.json with locked terms, restored tokens, and uncertainty status."""
    glossary_entries = glossary_data.get("entries", [])
    doc_status = segments_data.get("doc_status", "completed")
    uncertainties = list(segments_data.get("uncertainties", []))

    overrides: Dict[str, Dict[str, Any]] = {}
    if isinstance(translations_override, dict):
        if "segments" in translations_override and isinstance(translations_override["segments"], list):
            for item in translations_override["segments"]:
                if isinstance(item, dict) and item.get("segment_id"):
                    key = str(item["segment_id"])
                    if key in overrides:
                        uncertainties.append({"code": "duplicate_translation_id", "segment_id": key, "severity": "high", "message": "Two draft rows share this ID."})
                    overrides[key] = item
        else:
            for k, v in translations_override.items():
                if k not in ("contract_values", "glossary_additions"):
                    overrides[str(k)] = v if isinstance(v, dict) else {"target_text": v}

    unknown = set(overrides) - {seg["segment_id"] for seg in segments_data.get("segments", [])}
    for sid in sorted(unknown):
        uncertainties.append({"code": "unknown_translation_id", "segment_id": sid, "severity": "high",
                              "message": "No source segment matches this draft ID."})
    out_segments: List[Dict[str, Any]] = []
    title_en: Optional[str] = None
    title_zh: Optional[str] = None

    for seg in segments_data.get("segments", []):
        sid = seg["segment_id"]
        role = seg.get("role", "body")
        src = seg["source_text"]
        tokens = seg.get("protected_tokens", {})
        locked_used = [e["source_term"] for e in _find_locked_terms_in_source(src, glossary_entries)]

        ov = overrides.get(sid, {})
        supplied = ov.get("target_text")
        tgt = restore_protected_tokens(supplied.strip(), tokens) if isinstance(supplied, str) else ""
        clauses = ov.get("sentence_targets")
        if clauses is not None:
            if isinstance(clauses, list) and clauses and all(isinstance(v, str) and v.strip() for v in clauses):
                clauses = [restore_protected_tokens(v.strip(), tokens) for v in clauses]
                joined = "".join(clauses)
                if tgt and re.sub(r"\s+", "", tgt) != re.sub(r"\s+", "", joined):
                    uncertainties.append({"code": "conflicting_sentence_targets", "segment_id": sid, "severity": "high",
                                          "message": "sentence_targets do not reproduce target_text."})
                    clauses = []
                elif not tgt:
                    tgt = joined
            else:
                clauses = []
        rationale = str(ov.get("rationale") or "")
        if verbatim_allowed(seg, glossary_entries) and not tgt:
            tgt, seg_status = src, "verbatim"
            rationale = "原样保留文献标识、公式或无需转译的内容。"
        elif not tgt or copied_prose(seg, tgt, glossary_entries):
            reason = "missing_translation" if not tgt else "copied_source"
            tgt, seg_status = "", "untranslatable"
            rationale = "此段缺少可用中文译文，保留原文供补译。"
            uncertainties.append({"code": reason, "segment_id": sid, "pages": [seg["page"]], "message": rationale})
        elif ov.get("uncertain"):
            seg_status = "uncertain"
            uncertainties.append({"code": "translator_review", "segment_id": sid, "pages": [seg["page"]], "message": rationale or "译者标记此段待复核。"})
        else:
            seg_status = "verbatim" if tgt == src and verbatim_allowed(seg, glossary_entries) else "translated"

        if role == "title" and title_en is None:
            title_en = src
            title_zh = tgt

        out_segments.append(
            {
                "segment_id": sid,
                "block_id": seg["block_id"],
                "page": int(seg["page"]),
                "role": role,
                "section_path": seg.get("section_path", []),
                "source_text": src,
                "masked_text": seg.get("masked_text", src),
                "target_text": tgt,
                "rationale": rationale,
                "protected_tokens": tokens,
                "locked_terms_used": locked_used,
                "status": seg_status,
                "source_blocks": seg.get("source_blocks", []),
                "source_locator": seg.get("source_locator", {}),
                "sentence_alignment": align_sentences(seg, tgt, clauses),
                **({"table_id": seg["table_id"]} if "table_id" in seg else {}),
            }
        )

    by_id = {s["segment_id"]: s["target_text"] for s in out_segments}
    tables_bi = []
    for table in segments_data.get("tables_to_translate", []):
        ids = table.get("translation_ids", {})
        tables_bi.append({
            **table,
            "caption_en": table.get("caption") or table.get("label") or "",
            "caption_zh": by_id.get(ids.get("caption"), ""),
            "col_paths_en": table.get("col_paths", []),
            "col_paths_zh": [by_id.get(sid, "") for sid in ids.get("columns", [])],
            "rows_zh": [[by_id.get(sid, "") for sid in row] for row in ids.get("rows", [])],
            "notes_zh": [by_id.get(sid, "") for sid in ids.get("notes", [])],
        })
    has_unextracted_table = any(t.get("is_image_table") or t.get("status") not in ("extracted", "ok") for t in tables_bi)
    has_uncertain_seg = any(s["status"] in ("uncertain", "untranslatable") for s in out_segments)
    has_scan_uncertainty = any(
        u.get("code") in ("ocr_unavailable", "scanned_no_text", "garbled_font_cid") or u.get("severity") == "high"
        for u in uncertainties
    )

    if doc_status == "refused":
        final_status = "refused"
    elif doc_status == "partial" or has_unextracted_table or has_uncertain_seg or has_scan_uncertainty or len(out_segments) == 0:
        final_status = "partial"
    else:
        final_status = "completed"

    return {
        "schema_version": "reading-03-translation/v1",
        "status": final_status,
        "source_file": segments_data.get("source_file", ""),
        "source_lang": "en",
        "target_lang": "zh-CN",
        "title_en": title_en,
        "title_zh": title_zh,
        "glossary": glossary_data,
        "segments": out_segments,
        "tables_bilingual": tables_bi,
        "uncertainties": uncertainties,
        "qa_report": {},
    }


def write_rendered_outputs(bilingual_doc: Dict[str, Any], out_dir: Path) -> Dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    doc_p = out_dir / "bilingual_doc.json"
    md_p = out_dir / "bilingual.md"
    html_p = out_dir / "bilingual.html"
    zh_p = out_dir / "zh_only.md"
    write_json_atomic(doc_p, bilingual_doc)
    write_text_lf(md_p, _render_bilingual_markdown(bilingual_doc))
    write_text_lf(html_p, _render_bilingual_html(bilingual_doc))
    write_text_lf(zh_p, _render_zh_only(bilingual_doc))
    return {
        "bilingual_doc_json": str(doc_p),
        "bilingual_md": str(md_p),
        "bilingual_html": str(html_p),
        "zh_only_md": str(zh_p),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply locked glossary & render bilingual outputs.")
    parser.add_argument("--segments", required=True, help="Path to segments.json")
    parser.add_argument("--glossary", required=True, help="Path to glossary.json")
    parser.add_argument("--out-dir", required=True, help="Output directory for bilingual artifacts")
    parser.add_argument("--translations-json", default=None, help="Optional agent-provided translations JSON")
    args = parser.parse_args()

    segs = json.loads(Path(args.segments).read_text(encoding="utf-8"))
    glos = json.loads(Path(args.glossary).read_text(encoding="utf-8"))
    ov = None
    if args.translations_json and Path(args.translations_json).exists():
        ov = json.loads(Path(args.translations_json).read_text(encoding="utf-8"))

    bilingual = apply_translations(segs, glos, translations_override=ov)
    paths = write_rendered_outputs(bilingual, Path(args.out_dir))
    print(json.dumps({"status": bilingual["status"], "segment_count": len(bilingual["segments"]), **paths}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
