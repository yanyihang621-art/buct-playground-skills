#!/usr/bin/env python3
"""Apply glossary-locked bilingual translation and render JSON / Markdown / HTML views."""

from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from extract_terms import load_seed_glossary
from pdf_utils import write_json_atomic, write_text_lf

VERBATIM_ROLES = {"authors", "affiliation", "reference", "equation"}

CONNECTIVE_REWRITES: List[Tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bMoreover,\s*", re.I), "此外，"),
    (re.compile(r"\bFurthermore,\s*", re.I), "进一步地，"),
    (re.compile(r"\bHowever,\s*", re.I), "然而，"),
    (re.compile(r"\bTherefore,\s*", re.I), "因此，"),
    (re.compile(r"\bConsequently,\s*", re.I), "由此，"),
    (re.compile(r"\bIn contrast,\s*", re.I), "相比之下，"),
    (re.compile(r"\bAlthough\b", re.I), "尽管"),
    (re.compile(r"\bWhereas\b", re.I), "而"),
    (re.compile(r"\bBecause\b", re.I), "因为"),
    (re.compile(r"\bIn summary,\s*", re.I), "综上所述，"),
    (re.compile(r"\bIn this study,\s*", re.I), "在本研究中，"),
    (re.compile(r"\bHerein,\s*", re.I), "本文中，"),
    (re.compile(r"\bSpecifically,\s*", re.I), "具体而言，"),
]

COMMON_SCI_PHRASES: List[Tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bcatalyst\b", re.I), "催化剂"),
    (re.compile(r"\bcatalysts\b", re.I), "催化剂"),
    (re.compile(r"\bconversion\b", re.I), "转化率"),
    (re.compile(r"\byield\b", re.I), "产率"),
    (re.compile(r"\bstability\b", re.I), "稳定性"),
    (re.compile(r"\bperformance\b", re.I), "性能"),
    (re.compile(r"\btemperature\b", re.I), "温度"),
    (re.compile(r"\bpressure\b", re.I), "压力"),
    (re.compile(r"\bsynthesis\b", re.I), "合成"),
    (re.compile(r"\bcharacterization\b", re.I), "表征"),
    (re.compile(r"\bresults\s+(?:indicate|show|demonstrate|reveal)\s+that\b", re.I), "结果表明"),
    (re.compile(r"\bwas\s+not\s+observed\b", re.I), "未被观测到"),
    (re.compile(r"\bdid\s+not\s+exceed\b", re.I), "未超过"),
    (re.compile(r"\bcannot\s+be\b", re.I), "不能被"),
    (re.compile(r"\bwithout\b", re.I), "在无"),
    (re.compile(r"\bnever\b", re.I), "从未"),
    (re.compile(r"\bnot\b", re.I), "不"),
    (re.compile(r"\bonly\b", re.I), "仅"),
    (re.compile(r"\bexceeded\b", re.I), "超过了"),
    (re.compile(r"\bachieved\b", re.I), "达到了"),
    (re.compile(r"\bincreased\s+to\b", re.I), "提高至"),
    (re.compile(r"\bdecreased\s+to\b", re.I), "降低至"),
    (re.compile(r"\battributed\s+to\b", re.I), "归因于"),
    (re.compile(r"\bassociated\s+with\b", re.I), "与…相关"),
    (re.compile(r"\binhibited\b", re.I), "抑制了"),
    (re.compile(r"\bpromoted\b", re.I), "促进了"),
    (re.compile(r"\bexhibited\b", re.I), "表现出"),
    (re.compile(r"\bremained\s+stable\b", re.I), "保持稳定"),
    (re.compile(r"\bat\b", re.I), "在"),
    (re.compile(r"\band\b", re.I), "与"),
    (re.compile(r"\bwith\b", re.I), "具有"),
    (re.compile(r"\bunder\b", re.I), "在…条件下"),
]


def restore_protected_tokens(text: str, protected_tokens: Dict[str, str]) -> str:
    out = text
    for key, val in protected_tokens.items():
        out = out.replace(key, val)
    return out


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


def _deterministic_translate_segment(
    seg: Dict[str, Any],
    glossary_entries: List[Dict[str, Any]],
    heading_map: Dict[str, str],
) -> Tuple[str, str]:
    """Deterministic glossary-locked & clause-guided EN->ZH translation for offline checks/drafts."""
    role = seg.get("role", "body")
    src = seg.get("source_text", "")
    masked = seg.get("masked_text", src)
    tokens = seg.get("protected_tokens", {})

    if role in VERBATIM_ROLES:
        return src, f"角色 `{role}` 依规范保留原文。"

    if role == "keywords":
        # Replace known glossary terms inside keyword list
        out = masked
        for entry in glossary_entries:
            for v in [entry["source_term"]] + list(entry.get("aliases", [])):
                pat = re.compile(rf"(?<![A-Za-z0-9\-]){re.escape(v)}(?![A-Za-z0-9\-])", re.IGNORECASE)
                out = pat.sub(entry["target_zh"], out)
        out = re.sub(r"^(?:Keywords?|Index\s+Terms?)\s*[:：]\s*", "关键词：", out, flags=re.I)
        return restore_protected_tokens(out, tokens), "关键词列表按锁定术语表逐项译出。"

    if role == "heading":
        m = re.match(r"^(\d+(?:\.\d+)*\.?\s+)(.+)$", masked.strip())
        prefix = m.group(1) if m else ""
        body_h = m.group(2) if m else masked.strip()
        key = body_h.lower().rstrip(":").strip()
        if key in heading_map:
            zh_h = f"{prefix}{heading_map[key]}"
            return restore_protected_tokens(zh_h, tokens), "标准章节标题依学术体例本地化。"

    out = masked
    if role == "caption":
        out = re.sub(r"^(?:Fig\.|Figure)\s*(\d+)\.?\s*", r"图 \1. ", out, flags=re.I)
        out = re.sub(r"^(?:Table)\s*(\d+)\.?\s*", r"表 \1. ", out, flags=re.I)

    # Apply locked glossary longest-first to avoid partial replacement collisions
    ordered_entries = sorted(glossary_entries, key=lambda e: len(e["source_term"]), reverse=True)
    for entry in ordered_entries:
        variants = sorted([entry["source_term"]] + list(entry.get("aliases", [])), key=len, reverse=True)
        for v in variants:
            pat = re.compile(rf"(?<![A-Za-z0-9\-]){re.escape(v)}(?![A-Za-z0-9\-])", re.IGNORECASE)
            out = pat.sub(entry["target_zh"], out)

    for pat, repl in CONNECTIVE_REWRITES:
        out = pat.sub(repl, out)
    for pat, repl in COMMON_SCI_PHRASES:
        out = pat.sub(repl, out)

    out = restore_protected_tokens(out, tokens)
    out = re.sub(r"\s{2,}", " ", out).strip()
    return out, "依据锁定术语表、因果连接词及数值/公式占位符还原生成中文译文。"


def _translate_table(tab: Dict[str, Any], glossary_entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    cap_en = tab.get("caption") or tab.get("label") or tab.get("table_id") or "Table"
    cap_zh = re.sub(r"^Table\s+(\d+)\.?\s*", r"表 \1. ", cap_en, flags=re.I)
    for entry in sorted(glossary_entries, key=lambda e: len(e["source_term"]), reverse=True):
        pat = re.compile(rf"(?<![A-Za-z0-9\-]){re.escape(entry['source_term'])}(?![A-Za-z0-9\-])", re.I)
        cap_zh = pat.sub(entry["target_zh"], cap_zh)
    for pat, repl in COMMON_SCI_PHRASES:
        cap_zh = pat.sub(repl, cap_zh)

    cols_zh: List[str] = []
    for col in tab.get("col_paths", []):
        cz = str(col)
        for entry in glossary_entries:
            pat = re.compile(rf"(?<![A-Za-z0-9\-]){re.escape(entry['source_term'])}(?![A-Za-z0-9\-])", re.I)
            cz = pat.sub(entry["target_zh"], cz)
        for pat, repl in COMMON_SCI_PHRASES:
            cz = pat.sub(repl, cz)
        cols_zh.append(cz)

    return {
        "table_id": tab.get("table_id"),
        "pages": tab.get("pages", [1]),
        "caption_en": cap_en,
        "caption_zh": cap_zh,
        "col_paths_en": tab.get("col_paths", []),
        "col_paths_zh": cols_zh,
        "is_image_table": bool(tab.get("is_image_table", False)),
        "status": tab.get("status", "extracted"),
        "records": tab.get("records", []),
    }


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
            lines.append(f"- **[{u.get('severity', 'warning')}] {u.get('code', 'uncertainty')}** (pages {u.get('pages', [])}): {u.get('message', '')}")
        lines.append("")

    entries = doc.get("glossary", {}).get("entries", [])
    if entries:
        lines.append("## 锁定术语表 (Locked Terminology Glossary)")
        lines.append("| Term ID | 原文术语 (EN) | 锁定译名 (ZH) | 状态 | 首次出现 |")
        lines.append("|---|---|---|---|---|")
        for e in entries:
            fs = e.get("first_seen") or {}
            loc = f"p.{fs.get('page', 1)} `{fs.get('block_id', '')}`"
            lines.append(f"| `{e['term_id']}` | {e['source_term']} | {e['target_zh']} | `{e['status']}` | {loc} |")
        lines.append("")

    lines.append("## 双语段落对照 (Bilingual Aligned Segments)")
    lines.append("")
    for seg in doc.get("segments", []):
        anchor = f"[`{seg['segment_id']}` | p.{seg['page']} | `{seg['role']}` | `{seg['status']}`]"
        lines.append(f"### {anchor}")
        lines.append(f"- **EN:** {seg['source_text']}")
        lines.append(f"- **ZH:** {seg['target_text']}")
        if seg.get("rationale"):
            lines.append(f"- *说明:* {seg['rationale']}")
        lines.append("")

    if doc.get("tables_bilingual"):
        lines.append("## 图表标题与表头双语对照 (Tables Bilingual Mapping)")
        for t in doc["tables_bilingual"]:
            lines.append(f"### {t['table_id']} (Pages {t['pages']}) | Status: `{t['status']}`")
            lines.append(f"- **EN Caption:** {t['caption_en']}")
            lines.append(f"- **ZH Caption:** {t['caption_zh']}")
            if t.get("col_paths_en"):
                lines.append(f"- **Columns EN -> ZH:** `{' | '.join(t['col_paths_en'])}` -> `{' | '.join(t['col_paths_zh'])}`")
            lines.append("")

    return "\n".join(lines).strip() + "\n"


def _render_bilingual_html(doc: Dict[str, Any]) -> str:
    rows: List[str] = []
    for seg in doc.get("segments", []):
        rows.append(
            "<tr>"
            f"<td><code>{html_lib.escape(seg['segment_id'])}</code><br><small>p.{seg['page']} · {html_lib.escape(seg['role'])}</small></td>"
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
    for seg in doc.get("segments", []):
        role = seg.get("role", "body")
        txt = seg.get("target_text", "")
        if role == "title":
            continue
        if role == "heading":
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
    seed = load_seed_glossary(seed_path)
    heading_map = {str(k).lower(): str(v) for k, v in (seed.get("section_headings_zh") or {}).items()}
    glossary_entries = glossary_data.get("entries", [])
    doc_status = segments_data.get("doc_status", "completed")
    uncertainties = list(segments_data.get("uncertainties", []))

    overrides: Dict[str, Dict[str, Any]] = {}
    if isinstance(translations_override, dict):
        if "segments" in translations_override and isinstance(translations_override["segments"], list):
            for item in translations_override["segments"]:
                if isinstance(item, dict) and item.get("segment_id"):
                    overrides[str(item["segment_id"])] = item
        else:
            for k, v in translations_override.items():
                overrides[str(k)] = v if isinstance(v, dict) else {"target_text": str(v)}

    out_segments: List[Dict[str, Any]] = []
    title_en: Optional[str] = None
    title_zh: Optional[str] = None

    for seg in segments_data.get("segments", []):
        sid = seg["segment_id"]
        role = seg.get("role", "body")
        src = seg["source_text"]
        tokens = seg.get("protected_tokens", {})
        locked_used = [e["source_term"] for e in _find_locked_terms_in_source(src, glossary_entries)]

        if sid in overrides:
            ov = overrides[sid]
            tgt = restore_protected_tokens(str(ov.get("target_text", "")).strip(), tokens)
            rationale = str(ov.get("rationale", "人工/模型精译并执行锁定术语与占位符回填。"))
            seg_status = "uncertain" if ov.get("uncertain") else ("verbatim" if role in VERBATIM_ROLES else "translated")
            conf = float(ov.get("confidence", 0.96 if seg_status == "translated" else 0.7))
        else:
            tgt, rationale = _deterministic_translate_segment(seg, glossary_entries, heading_map)
            seg_status = "verbatim" if role in VERBATIM_ROLES else "translated"
            conf = 0.98 if role in VERBATIM_ROLES else 0.85

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
                "confidence": round(conf, 3),
            }
        )

    tables_bi = [_translate_table(t, glossary_entries) for t in segments_data.get("tables_to_translate", [])]
    has_unextracted_table = any(t.get("is_image_table") or t.get("status") not in ("extracted", "ok") for t in tables_bi)
    has_uncertain_seg = any(s["status"] == "uncertain" for s in out_segments)
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
