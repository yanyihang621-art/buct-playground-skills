"""Shared evidence normalization, anchor resolution, and factual guardrails."""

from __future__ import annotations

import difflib
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

_LIGATURES = str.maketrans({
    "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl",
    "\ufb03": "ffi", "\ufb04": "ffl", "\ufb05": "ft", "\ufb06": "st",
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2015": "-", "\u2212": "-",
    "\u2018": "'", "\u2019": "'", "\u2032": "'",
    "\u201c": '"', "\u201d": '"', "\u00a0": " ",
})

_NEG_EN_RE = re.compile(
    r"\b(?:not(?!\s+only\b)|no|never|neither|nor|none|nothing|nowhere|without|cannot|can't|don't|doesn't|didn't|"
    r"won't|wouldn't|isn't|aren't|wasn't|weren't|fail(?:s|ed)?\s+to|lack(?:s|ed)?\s+of|absence\s+of|"
    r"no\s+significant)\b",
    re.I,
)
_NEG_ZH_RE = re.compile(r"(?:未|不(?!可逆|同|确定|变性|变量|稳定|均匀|规则)|无|没有|并非|未能|无效|无显著|缺乏|难以|无法)")

_HEDGE_EN_RE = re.compile(
    r"\b(?:may|might|could|suggest(?:s|ed|ing)?|likely|possibly|perhaps|appear(?:s|ed)?\s+to|"
    r"seem(?:s|ed)?\s+to|hypothesiz(?:e|es|ed)|indicat(?:e|es|ed)|potential(?:ly)?|tentative(?:ly)?)\b",
    re.I,
)
_HEDGE_ZH_RE = re.compile(r"(?:可能|或许|推测|提示|似乎|似需|倾向|初步|假说|假设|潜在|预计|有望)")

_NUM_RE = re.compile(r"(?<![A-Za-z0-9_^])[-+]?\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?%?")
_IGNORED_NUM_CTX_RE = re.compile(
    r"(?:paper_|sec_|tab_|fig_|eq_|p\d+_b\d+|page\s+\d+|p\.\s*\d+|§\s*\d+(?:\.\d+)*|"
    r"(?:Table|Figure|Fig\.|Section|Eq\.|Equation|图|表|第)\s*\d+|"
    r"\[\d+(?:\s*,\s*\d+)*\]|\b(?:19|20)\d{2}[a-z]?\b)",
    re.I,
)

_QUOTE_KEYS = ("quote", "quote_snippet", "evidence_quote", "snippet", "evidence_text", "source_text")
_PAGE_KEYS = ("page", "pdf_page", "page_number")
_ANCHOR_KEYS = ("anchor", "element_id", "block_id", "table_id", "figure_id", "eq_id")


def normalize_text(text: str) -> str:
    """Normalize unicode, ligatures, punctuation, line-wrap hyphens, and whitespace."""
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", text).translate(_LIGATURES)
    s = re.sub(r"[\u00ad\u200b-\u200d\ufeff]", "", s)
    s = re.sub(r"([A-Za-z])-\s+([A-Za-z])", r"\1\2", s)
    s = re.sub(r"\s+", " ", s.lower()).strip()
    return s


def section_title_map(doc: Dict[str, Any]) -> Tuple[Dict[str, str], List[Dict[str, Any]]]:
    """Flatten hierarchical sections into {section_id: display_title} and ordered outline."""
    titles: Dict[str, str] = {}
    outline: List[Dict[str, Any]] = []

    def walk(nodes: Sequence[Dict[str, Any]]) -> None:
        for sec in nodes:
            sid = sec.get("section_id") or ""
            num = (sec.get("number") or "").strip()
            raw_title = (sec.get("title") or "").strip()
            full = f"{num} {raw_title}".strip() if num and not raw_title.startswith(num) else (raw_title or num)
            if sid:
                titles[sid] = full
                outline.append({
                    "section_id": sid,
                    "level": sec.get("level", 1),
                    "number": num,
                    "title": full,
                    "page_start": sec.get("page_start"),
                    "page_end": sec.get("page_end"),
                })
            walk(sec.get("children", []))

    walk(doc.get("sections", []))
    return titles, outline


def source_elements(doc: Dict[str, Any]) -> Iterable[Tuple[str, str, str, Optional[str], Optional[int], Any, Any]]:
    """Yield (kind, eid, text, section_id, page, bbox, native_locator) in document order."""
    for block in doc.get("blocks", []):
        text = block.get("structured_text") or block.get("text") or ""
        yield ("block", block["block_id"], text, block.get("section_id"),
               block.get("page"), block.get("bbox"), block.get("native_locator"))
    for table in doc.get("tables", []):
        content = "\n".join(filter(None, (table.get("caption"), table.get("markdown"))))
        page = next(iter(table.get("pages", [])), None)
        yield ("table", table["table_id"], content, table.get("section_id"),
               page, table.get("bbox"), None)
    for figure in doc.get("figures", []):
        yield ("figure", figure["figure_id"], figure.get("caption") or "",
               figure.get("section_id"), figure.get("page"), figure.get("caption_bbox"), None)
    for eq in doc.get("formulas", {}).get("display_equations", []):
        yield ("eq", eq["eq_id"], eq.get("structured_text") or "",
               eq.get("section_id"), eq.get("page"), eq.get("bbox"), None)


def parse_anchor(anchor: str, default_paper: str = "paper_1") -> Tuple[str, str]:
    """Split optional 'paper_id:anchor_id' and strip optional 'pN_' table/fig/eq prefix."""
    raw = (anchor or "").strip()
    if ":" in raw:
        pid, eid = raw.split(":", 1)
        pid = pid.strip() or default_paper
        eid = eid.strip()
    else:
        pid, eid = default_paper, raw
    return pid, eid


def _canonical_eid(eid: str) -> str:
    m = re.match(r"^p\d+_((?:tab|fig|eq)_\d+)$", eid)
    return m.group(1) if m else eid


class IndexStore:
    """Fast lookup wrapper over work/index.json entries."""

    def __init__(self, entries: List[Dict[str, Any]]) -> None:
        self.entries = entries
        self.by_key: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self.by_paper: Dict[str, List[Dict[str, Any]]] = {}
        self.norm_texts: List[str] = []
        for idx, item in enumerate(entries):
            pid = item.get("paper_id", "paper_1")
            anc = item.get("anchor", "")
            self.by_key[(pid, anc)] = item
            self.by_paper.setdefault(pid, []).append(item)
            item["_idx"] = idx
            self.norm_texts.append(normalize_text(item.get("text", "")))

    def get(self, paper_id: str, anchor: str) -> Optional[Dict[str, Any]]:
        item = self.by_key.get((paper_id, anchor))
        if item is not None:
            return item
        canon = _canonical_eid(anchor)
        return self.by_key.get((paper_id, canon))


def _fuzzy_score(norm_q: str, norm_t: str) -> float:
    if not norm_q or not norm_t:
        return 0.0
    if norm_q in norm_t:
        return 1.0
    q_len = len(norm_q)
    if len(norm_t) <= q_len + 24:
        return difflib.SequenceMatcher(None, norm_q, norm_t).ratio()
    # Slide over word boundaries near token matches to keep verification fast.
    q_tokens = set(norm_q.split())
    words = norm_t.split()
    best = 0.0
    win_words = max(4, len(norm_q.split()) + 3)
    step = max(1, win_words // 3)
    for start in range(0, len(words), step):
        chunk = " ".join(words[start:start + win_words])
        if q_tokens and len(q_tokens & set(chunk.split())) / len(q_tokens) < 0.5:
            continue
        ratio = difflib.SequenceMatcher(None, norm_q, chunk).ratio()
        if ratio > best:
            best = ratio
            if best >= 0.96:
                break
    return best


def verify(quote: str, anchor: str, paper_id: str, index: Any) -> Dict[str, Any]:
    """Verify a verbatim quote against index entries and relocate if needed."""
    store = index if isinstance(index, IndexStore) else IndexStore(index)
    pid, eid = parse_anchor(anchor, paper_id or "paper_1")
    norm_q = normalize_text(quote)
    target = store.get(pid, eid)

    is_table_or_num = (
        (target is not None and target.get("kind") == "table")
        or bool(re.search(r"\d", norm_q))
    )
    if not norm_q or (len(norm_q) < 12 and not is_table_or_num):
        return {
            "match": "too_short",
            "paper_id": pid,
            "anchor": target["anchor"] if target else eid,
            "page": target.get("page") if target else None,
            "section_id": target.get("section_id") if target else None,
            "section_title": target.get("section_title") if target else None,
            "block_text": target.get("text", "") if target else "",
        }

    if target is not None:
        t_norm = store.norm_texts[target["_idx"]]
        if norm_q in t_norm:
            return {
                "match": "exact",
                "paper_id": pid,
                "anchor": target["anchor"],
                "page": target.get("page"),
                "section_id": target.get("section_id"),
                "section_title": target.get("section_title"),
                "block_text": target.get("text", ""),
            }

    # Search same paper first, then other papers.
    candidates = list(store.by_paper.get(pid, []))
    for other_pid, items in store.by_paper.items():
        if other_pid != pid:
            candidates.extend(items)

    for item in candidates:
        if norm_q in store.norm_texts[item["_idx"]]:
            is_same_target = target is not None and item["_idx"] == target["_idx"]
            return {
                "match": "exact" if is_same_target else "relocated",
                "relocated_from": None if is_same_target else (f"{pid}:{eid}" if anchor else None),
                "paper_id": item["paper_id"],
                "anchor": item["anchor"],
                "page": item.get("page"),
                "section_id": item.get("section_id"),
                "section_title": item.get("section_title"),
                "block_text": item.get("text", ""),
            }

    # Fuzzy fallback on target first, then top token-overlapping blocks.
    best_ratio = 0.0
    best_item: Optional[Dict[str, Any]] = None
    if target is not None:
        r = _fuzzy_score(norm_q, store.norm_texts[target["_idx"]])
        best_ratio, best_item = r, target

    if best_ratio < 0.90:
        q_tokens = set(norm_q.split())
        scored = []
        for item in candidates:
            t_norm = store.norm_texts[item["_idx"]]
            if not t_norm:
                continue
            ov = len(q_tokens & set(t_norm.split())) if q_tokens else 0
            if ov > 0:
                scored.append((ov, item))
        scored.sort(key=lambda x: -x[0])
        for _, item in scored[:15]:
            r = _fuzzy_score(norm_q, store.norm_texts[item["_idx"]])
            if r > best_ratio:
                best_ratio, best_item = r, item
                if best_ratio >= 0.95:
                    break

    if best_item is not None and best_ratio >= 0.90:
        return {
            "match": "fuzzy",
            "similarity": round(best_ratio, 3),
            "paper_id": best_item["paper_id"],
            "anchor": best_item["anchor"],
            "page": best_item.get("page"),
            "section_id": best_item.get("section_id"),
            "section_title": best_item.get("section_title"),
            "block_text": best_item.get("text", ""),
        }

    return {
        "match": "unverified",
        "paper_id": pid,
        "anchor": eid,
        "page": target.get("page") if target else None,
        "section_id": target.get("section_id") if target else None,
        "section_title": target.get("section_title") if target else None,
        "block_text": target.get("text", "") if target else "",
    }


def _extract_numeric_tokens(text: str) -> List[str]:
    if not text:
        return []
    cleaned = _IGNORED_NUM_CTX_RE.sub(" ", text)
    nums: List[str] = []
    for m in _NUM_RE.finditer(cleaned):
        raw = m.group(0).replace(",", "").rstrip("%")
        if raw in {"+", "-", "."}:
            continue
        try:
            val = float(raw)
        except ValueError:
            continue
        nums.append(f"{val:g}")
    return nums


def check_numbers(text: str, evidence_texts: Iterable[str]) -> List[str]:
    """Return numeric values present in text but missing from all evidence texts."""
    if not text:
        return []
    ev_joined = " \n ".join(t for t in evidence_texts if t)
    ev_nums = set(_extract_numeric_tokens(ev_joined))
    ev_norm = normalize_text(ev_joined)
    missing: List[str] = []
    for m in _NUM_RE.finditer(_IGNORED_NUM_CTX_RE.sub(" ", text)):
        raw = m.group(0)
        canon = raw.replace(",", "").rstrip("%")
        try:
            norm_num = f"{float(canon):g}"
        except ValueError:
            continue
        if norm_num not in ev_nums and canon.lstrip("+") not in ev_norm:
            if raw not in missing:
                missing.append(raw)
    return missing


def has_negation(text: str) -> bool:
    return bool(text and (_NEG_EN_RE.search(text) or _NEG_ZH_RE.search(text)))


def has_hedge(text: str) -> bool:
    return bool(text and (_HEDGE_EN_RE.search(text) or _HEDGE_ZH_RE.search(text)))


def check_negation_and_hedge(text: str, quote: str) -> List[str]:
    """Flag dropped negation or dropped hedging relative to the source quote."""
    if not text or not quote:
        return []
    flags: List[str] = []
    if has_negation(quote) and not has_negation(text):
        flags.append("negation_dropped")
    if has_hedge(quote) and not has_hedge(text):
        flags.append("hedge_dropped")
    return flags


def format_anchor(paper_id: str, anchor: str, multi_paper: bool) -> str:
    return f"{paper_id}:{anchor}" if multi_paper and ":" not in anchor else anchor


def scan_fields_evidence(
    obj: Any,
    default_paper: str,
    store: IndexStore,
    multi_paper: bool,
    change_records: List[Dict[str, Any]],
    warnings: List[Dict[str, Any]],
    path: str = "fields",
) -> None:
    """Recursively verify quote-bearing objects inside answer.fields and sync page/anchor."""
    if isinstance(obj, list):
        for i, item in enumerate(obj):
            scan_fields_evidence(item, default_paper, store, multi_paper, change_records, warnings, f"{path}[{i}]")
        return
    if not isinstance(obj, dict):
        return

    quote_key = next((k for k in _QUOTE_KEYS if isinstance(obj.get(k), str) and obj[k].strip()), None)
    if quote_key is not None:
        anchor_key = next((k for k in _ANCHOR_KEYS if isinstance(obj.get(k), str) and obj[k].strip()), None)
        anchor_val = obj[anchor_key] if anchor_key else ""
        res = verify(obj[quote_key], anchor_val, default_paper, store)
        if res["match"] in {"unverified", "too_short"}:
            warnings.append({
                "code": f"field_quote_{res['match']}",
                "path": path,
                "quote": obj[quote_key][:120],
                "anchor": anchor_val,
            })
        else:
            if res["match"] == "relocated" and anchor_key:
                new_anc = format_anchor(res["paper_id"], res["anchor"], multi_paper)
                if new_anc != anchor_val:
                    change_records.append({
                        "type": "anchor_relocated",
                        "path": f"{path}.{anchor_key}",
                        "old": anchor_val,
                        "new": new_anc,
                    })
                    obj[anchor_key] = new_anc
            page_key = next((k for k in _PAGE_KEYS if k in obj), None)
            if page_key and res.get("page") is not None:
                old_p = obj.get(page_key)
                if old_p in (None, "", 0):
                    obj[page_key] = res["page"]
                    change_records.append({
                        "type": "page_filled",
                        "path": f"{path}.{page_key}",
                        "old": old_p,
                        "new": res["page"],
                    })
                elif old_p != res["page"]:
                    obj[page_key] = res["page"]
                    change_records.append({
                        "type": "page_corrected",
                        "path": f"{path}.{page_key}",
                        "old": old_p,
                        "new": res["page"],
                    })

    for k, v in obj.items():
        if isinstance(v, (dict, list)):
            scan_fields_evidence(v, default_paper, store, multi_paper, change_records, warnings, f"{path}.{k}")
