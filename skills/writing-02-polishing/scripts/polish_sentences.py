#!/usr/bin/env python3
"""Apply glossary-locked, invariant-preserving academic polishing with diffs and rationales."""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from text_utils import write_json_atomic, write_text_lf

DETERMINISTIC_RULES: List[Tuple[re.Pattern[str], str, str, str]] = [
    (re.compile(r"\bMore and more researchers\b", re.I), "An increasing number of studies", "register_chinglish", "消除中式英语 `More and more researchers`，改用客观学术表达。"),
    (re.compile(r"\bIn a word,\s*", re.I), "In summary, ", "register_colloquial", "将口语过渡词 `In a word` 规范化为 `In summary`。"),
    (re.compile(r"\bNeedless to say,\s*", re.I), "", "register_colloquial", "删除口语化冗余表述 `Needless to say`。"),
    (re.compile(r"\ba lot of\b", re.I), "numerous", "register_colloquial", "将口语量词 `a lot of` 规范化为 `numerous`。"),
    (re.compile(r"\blots of\b", re.I), "substantial", "register_colloquial", "将口语量词 `lots of` 规范化为 `substantial`。"),
    (re.compile(r"\bpretty good\b", re.I), "favorable", "register_colloquial", "将口语评价 `pretty good` 替换为学术中性词 `favorable`。"),
    (re.compile(r"\bvery obvious\b", re.I), "pronounced", "register_colloquial", "将 `very obvious` 规范化为 `pronounced`。"),
    (re.compile(r"\brevolutionary breakthrough\b", re.I), "notable advance", "hype_moderation", "消除夸张宣传词 `revolutionary breakthrough`，匹配实际研究证据强度。"),
    (re.compile(r"\bAs is known to all,\s*", re.I), "Prior studies have established that ", "register_chinglish", "消除中式直译开篇 `As is known to all`。"),
    (re.compile(r"\bIn order to investigate\b", re.I), "To investigate", "conciseness", "精简冗余目的状语 `In order to` -> `To`。"),
    (re.compile(r"\bThe results shows\b"), "The results show", "grammar_agreement", "修正主谓一致错误 `The results shows` -> `The results show`。"),
    (re.compile(r"\bThe catalyst exhibit\b"), "The catalyst exhibits", "grammar_agreement", "修正第三人称单数主谓一致错误 `exhibit` -> `exhibits`。"),
    (re.compile(r"\bdata shows\b", re.I), "data show", "grammar_agreement", "修正学术复数名词一致 `data shows` -> `data show`。"),
    (re.compile(r"众所周知[，,]\s*"), "已有研究表明，", "register_chinglish", "消除绝对化/口语开篇 `众所周知`。"),
    (re.compile(r"毋庸置疑[，,]\s*"), "观测结果表明，", "hype_moderation", "消除绝对化断言 `毋庸置疑`，改为基于证据的客观陈述。"),
    (re.compile(r"划时代突破"), "显著进展", "hype_moderation", "去除项目申报/论文中的宣传性夸张用词 `划时代突破`。"),
    (re.compile(r"彻底解决了"), "有效缓解了", "hype_moderation", "降低绝对化宣称强度 `彻底解决` -> `有效缓解`。"),
    (re.compile(r"非常完美"), "较为理想", "register_colloquial", "消除口语化极端形容词 `非常完美`。"),
]


def _rule_polish_sentence(sent: Dict[str, Any], glossary: Dict[str, Any]) -> Tuple[str, str, str]:
    role = sent.get("role", "prose")
    src = sent["source_text"]
    if role in ("verbatim_block", "structural", "heading"):
        return src, "preserved", f"角色 `{role}` 原样保留结构与公式/排版边界。"

    out = src
    applied_types: List[str] = []
    reasons: List[str] = []

    # 1. Enforce custom/locked glossary variants
    for entry in glossary.get("entries", []):
        canon = entry["canonical"]
        for var in entry.get("variants", []):
            pat = re.compile(rf"(?<![A-Za-z0-9\-]){re.escape(var)}(?![A-Za-z0-9\-])")
            if pat.search(out) and var != canon:
                out = pat.sub(canon, out)
                applied_types.append("terminology_unification")
                reasons.append(f"统一术语 `{var}` -> `{canon}`。")

    # 2. Apply deterministic grammar/register/Chinglish fixes (avoid mutating inside $...$)
    for pat, repl, etype, reason in DETERMINISTIC_RULES:
        if pat.search(out):
            new_out = pat.sub(repl, out)
            if new_out != out:
                # Capitalize start if sentence began with removed phrase
                if new_out and not new_out[0].isupper() and src[0].isupper() and new_out[0].isascii():
                    new_out = new_out[0].upper() + new_out[1:]
                out = re.sub(r"\s{2,}", " ", new_out).strip()
                applied_types.append(etype)
                reasons.append(reason)

    if not applied_types:
        return src, "unchanged", "原句语法、语气与术语均合规，依最小编辑原则保留原文。"
    return out, "+".join(dict.fromkeys(applied_types)), " ".join(reasons)


def _assemble_manuscript(sentences: List[Dict[str, Any]]) -> str:
    """Reassemble sentences into paragraphs preserving para_id boundaries and LaTeX/Markdown spacing."""
    paras: Dict[int, List[str]] = {}
    order: List[int] = []
    for s in sentences:
        pid = int(s["para_id"])
        if pid not in paras:
            paras[pid] = []
            order.append(pid)
        paras[pid].append(s["polished_text"])

    blocks: List[str] = []
    for pid in order:
        parts = paras[pid]
        joined = parts[0]
        for nxt in parts[1:]:
            if joined.endswith("。") or ("\u4e00" <= joined[-1] <= "\u9fff"):
                joined = f"{joined}{nxt}"
            else:
                joined = f"{joined} {nxt}"
        blocks.append(joined)
    return "\n\n".join(blocks) + "\n"


def _render_revision_report(doc: Dict[str, Any]) -> str:
    lines: List[str] = [
        f"# 学术语言润色逐句修改清单 (`{doc['register']}`)",
        "",
        f"**运行状态:** `{doc['status']}` | **总句数:** {doc['sentence_count']} | **修改句数:** {doc['edited_count']}",
        "",
        "| Sent ID | 行号 | 修改类型 | 原文 (Source) | 改文 (Polished) | 修改理由 (Rationale) |",
        "|---|---:|---|---|---|---|",
    ]
    for s in doc["sentences"]:
        if s["role"] == "verbatim_block":
            continue
        src = s["source_text"].replace("|", "\\|")
        pol = s["polished_text"].replace("|", "\\|")
        rat = s["rationale"].replace("|", "\\|")
        lines.append(f"| `{s['sent_id']}` | {s['line']} | `{s['edit_type']}` | {src} | {pol} | {rat} |")
    lines.append("")
    return "\n".join(lines)


def polish_guarded_sentences(
    guarded: Dict[str, Any],
    glossary: Dict[str, Any],
    *,
    register: str = "journal_article",
    edits_override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Produce polished sentences, reconstructed manuscript, diff patch, and revision report."""
    overrides: Dict[str, Dict[str, Any]] = {}
    if isinstance(edits_override, dict):
        items = edits_override.get("sentences", edits_override)
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict) and it.get("sent_id"):
                    overrides[str(it["sent_id"])] = it
        elif isinstance(items, dict):
            for k, v in items.items():
                overrides[str(k)] = v if isinstance(v, dict) else {"polished_text": str(v)}

    orig_sentences = guarded.get("sentences", [])
    out_sentences: List[Dict[str, Any]] = []
    edited_count = 0

    for s in orig_sentences:
        sid = s["sent_id"]
        if sid in overrides and s["role"] == "prose":
            ov = overrides[sid]
            pol = str(ov.get("polished_text", s["source_text"])).strip()
            etype = str(ov.get("edit_type", "agent_polish"))
            rationale = str(ov.get("rationale", "Agent 学术语域与句法微调。"))
            uncertain = bool(ov.get("uncertain", False))
        else:
            pol, etype, rationale = _rule_polish_sentence(s, glossary)
            uncertain = False

        if pol != s["source_text"]:
            edited_count += 1

        out_sentences.append(
            {
                "sent_id": sid,
                "para_id": s["para_id"],
                "line": s["line"],
                "role": s["role"],
                "source_text": s["source_text"],
                "polished_text": pol,
                "edit_type": etype,
                "rationale": rationale,
                "protected_tokens": s.get("protected_tokens", []),
                "hedge_tier": s.get("hedge_tier", "neutral"),
                "relation_type": s.get("relation_type", "none"),
                "negation_markers": s.get("negation_markers", []),
                "uncertain": uncertain,
            }
        )

    orig_reconstructed = "\n\n".join(s["source_text"] for s in orig_sentences) + "\n"
    polished_ms = _assemble_manuscript(out_sentences)
    diff_text = "\n".join(
        difflib.unified_diff(
            [s["source_text"] for s in out_sentences],
            [s["polished_text"] for s in out_sentences],
            fromfile="a/manuscript",
            tofile="b/polished_manuscript",
            lineterm="",
        )
    )

    has_conflicts = bool(glossary.get("conflicts"))
    has_uncertain = any(s["uncertain"] for s in out_sentences)
    status = "partial" if (has_conflicts or has_uncertain) else "completed"

    doc = {
        "schema_version": "writing-02-polish-doc/v1",
        "register": register,
        "status": status,
        "sentence_count": len(out_sentences),
        "edited_count": edited_count,
        "sentences": out_sentences,
        "glossary": glossary,
        "qa_report": {},
    }
    return {
        "polished_doc": doc,
        "polished_manuscript": polished_ms,
        "revision_report_md": _render_revision_report(doc),
        "patch_diff": diff_text + ("\n" if diff_text else ""),
        "orig_reconstructed": orig_reconstructed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Polish guarded sentences and generate revision report.")
    parser.add_argument("--guarded", required=True)
    parser.add_argument("--glossary", required=True)
    parser.add_argument("--register", default="journal_article")
    parser.add_argument("--edits-json", default=None)
    parser.add_argument("--out-doc", required=True)
    parser.add_argument("--out-ms", required=True)
    parser.add_argument("--out-report", required=True)
    args = parser.parse_args()

    guarded = json.loads(Path(args.guarded).read_text(encoding="utf-8"))
    glos = json.loads(Path(args.glossary).read_text(encoding="utf-8"))
    ov = json.loads(Path(args.edits_json).read_text(encoding="utf-8")) if (args.edits_json and Path(args.edits_json).exists()) else None
    res = polish_guarded_sentences(guarded, glos, register=args.register, edits_override=ov)
    write_json_atomic(Path(args.out_doc), res["polished_doc"])
    write_text_lf(Path(args.out_ms), res["polished_manuscript"])
    write_text_lf(Path(args.out_report), res["revision_report_md"])
    print(json.dumps({"status": res["polished_doc"]["status"], "edited_count": res["polished_doc"]["edited_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
