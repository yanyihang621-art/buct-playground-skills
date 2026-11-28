"""Shared evidence normalization, anchor resolution, unit checks, and factual guardrails."""

from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

_LIGATURES = str.maketrans({
    "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl",
    "\ufb03": "ffi", "\ufb04": "ffl", "\ufb05": "ft", "\ufb06": "st",
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2015": "-", "\u2212": "-",
    "\u2018": "'", "\u2019": "'", "\u2032": "'",
    "\u201c": '"', "\u201d": '"', "\u00a0": " ",
})

_NEG_EN_RE = re.compile(
    r"\b(?:not(?!\s+only\b)|no(?!\s+more\s+than\b)|never|neither|nor|none|nothing|nowhere|"
    r"without(?!\s+(?:layer\s+|batch\s+|weight\s+)?normalization\b)|"
    r"cannot|can't|don't|doesn't|didn't|won't|wouldn't|isn't|aren't|wasn't|weren't|"
    r"fail(?:s|ed)?\s+to|lack(?:s|ed)?\s+of|absence\s+of|no\s+significant)\b",
    re.I,
)
_NEG_BASELINE_WITHOUT_RE = re.compile(r"\b(?:baseline|model|variant)\s+without\b", re.I)
_NEG_ZH_RE = re.compile(r"(?:未|不(?!可逆|同|确定|变性|变量|稳定|均匀|规则)|无(?!归一化)|没有|并非|未能|无效|无显著|缺乏|难以|无法)")

_HEDGE_EN_RE = re.compile(
    r"\b(?:may|might|could|suggest(?:s|ed|ing)?|likely|possibly|perhaps|appear(?:s|ed)?\s+to|"
    r"seem(?:s|ed)?\s+to|hypothesiz(?:e|es|ed)|indicat(?:e|es|ed)|potential(?:ly)?|tentative(?:ly)?)\b",
    re.I,
)
_HEDGE_ZH_RE = re.compile(r"(?:可能|或许|推测|提示|似乎|似需|倾向|初步|假说|假设|潜在|预计|有望)")

_NUM_RE = re.compile(r"(?<![A-Za-z0-9_^])[-+]?\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?%?")
_MAG_NUM_RE = re.compile(
    r"(?<![A-Za-z0-9_^])([-+]?\d[\d,]*(?:\.\d+)?)\s*"
    r"(million|billion|thousand|百万|十亿|万|亿|[kKB]|M(?=\s*(?:iterations?|iters?|steps?|samples?|tokens?|epoches?|epochs?|examples?|params?|parameters?|次|步|条)\b|\b))",
    re.I,
)
_POW10_RE = re.compile(r"([-+]?\d[\d,]*(?:\.\d+)?)\s*[×x*]\s*10\s*\^\s*\{?([-+]?\d+)\}?", re.I)
_IGNORED_NUM_CTX_RE = re.compile(
    r"(?:paper_|sec_|tab_|fig_|eq_|p\d+_b\d+|page\s+\d+|p\.\s*\d+|§\s*\d+(?:\.\d+)*|"
    r"(?:Table|Figure|Fig\.|Section|Eq\.|Equation|图|表|第)\s*\d+|"
    r"\[\d+(?:\s*,\s*\d+)*\]|\b(?:19|20)\d{2}[a-z]?\b)",
    re.I,
)

# Physical unit extraction (case-sensitive where needed, e.g. mW vs MW, mM vs M)
_UNIT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])([-+]?\d[\d,]*(?:\.\d+)?)\s*"
    r"(GHz|MHz|kHz|Hz|ms|min|hrs?|s|nm|um|μm|mm|cm|m|ug|μg|mg|kg|g|kV|mV|V|mA|A|MW|kW|mW|W|GPa|MPa|kPa|Pa|atm|bar|torr|°C|℃|K|vol%|wt%|ppm|ppb|%|mM|μM|uM|M(?=\s|$)|keV|MeV|eV|kJ|MJ|J)(?![A-Za-z0-9_%℃])"
)
_UNIT_FAMILIES: Dict[str, Tuple[str, str]] = {
    "hz": ("frequency", "Hz"), "khz": ("frequency", "kHz"), "mhz": ("frequency", "MHz"), "ghz": ("frequency", "GHz"),
    "ms": ("time", "ms"), "s": ("time", "s"), "min": ("time", "min"), "h": ("time", "h"), "hr": ("time", "h"), "hrs": ("time", "h"),
    "nm": ("length", "nm"), "um": ("length", "um"), "μm": ("length", "um"), "mm": ("length", "mm"), "cm": ("length", "cm"), "m": ("length", "m"),
    "ug": ("mass", "ug"), "μg": ("mass", "ug"), "mg": ("mass", "mg"), "g": ("mass", "g"), "kg": ("mass", "kg"),
    "mv": ("voltage", "mV"), "v": ("voltage", "V"), "kv": ("voltage", "kV"),
    "ma": ("current", "mA"), "a": ("current", "A"),
    "mw_low": ("power", "mW"), "w": ("power", "W"), "kw": ("power", "kW"), "MW_up": ("power", "MW"),
    "pa": ("pressure", "Pa"), "kpa": ("pressure", "kPa"), "mpa": ("pressure", "MPa"), "gpa": ("pressure", "GPa"),
    "bar": ("pressure", "bar"), "atm": ("pressure", "atm"), "torr": ("pressure", "torr"),
    "k": ("temp", "K"), "°c": ("temp", "°C"), "℃": ("temp", "°C"),
    "%": ("percent", "%"), "vol%": ("percent", "vol%"), "wt%": ("percent", "wt%"), "ppm": ("conc", "ppm"), "ppb": ("conc", "ppb"),
    "mm_conc": ("molarity", "mM"), "um_conc": ("molarity", "μM"), "M_conc": ("molarity", "M"),
    "ev": ("energy", "eV"), "kev": ("energy", "keV"), "mev": ("energy", "MeV"),
    "j": ("energy", "J"), "kj": ("energy", "kJ"), "mj": ("energy", "MJ"),
}

_QUOTE_KEYS = ("quote", "quote_snippet", "evidence_quote", "snippet", "evidence_text", "source_text")
_PAGE_KEYS = ("page", "pdf_page", "page_number")
_ANCHOR_KEYS = ("anchor", "element_id", "block_id", "table_id", "figure_id", "eq_id")
_TEXT_FIELD_KEYS = ("answer", "summary", "claim", "statement", "comparison", "text", "finding")


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
    """Yield (kind, eid, text, section_id, page, bbox, native_locator) with tables inlined in reading order."""
    tables_by_id = {t["table_id"]: t for t in doc.get("tables", []) if t.get("table_id")}
    emitted_tables: set[str] = set()
    unanchored_by_sec: Dict[str, List[Dict[str, Any]]] = {}
    anchored_tids = {b.get("table_id") for b in doc.get("blocks", []) if b.get("role") == "table_ref" and b.get("table_id")}
    for t in doc.get("tables", []):
        tid = t.get("table_id")
        if tid and tid not in anchored_tids:
            unanchored_by_sec.setdefault(t.get("section_id") or "__tail__", []).append(t)

    def _emit_table(tab: Dict[str, Any], fallback_sec: Optional[str]) -> Tuple[str, str, str, Optional[str], Optional[int], Any, Any]:
        content = "\n".join(filter(None, (tab.get("caption"), tab.get("markdown"))))
        if tab.get("notes"):
            content = "\n".join(filter(None, (content, "Notes: " + " ".join(tab["notes"]))))
        page = next(iter(tab.get("pages", [])), None)
        return ("table", tab["table_id"], content, tab.get("section_id") or fallback_sec, page, tab.get("bbox"), None)

    blocks = doc.get("blocks", [])
    for idx, block in enumerate(blocks):
        if block.get("role") == "table_ref":
            tid = block.get("table_id")
            if tid and tid in tables_by_id and tid not in emitted_tables:
                emitted_tables.add(tid)
                yield _emit_table(tables_by_id[tid], block.get("section_id"))
                continue
        text = block.get("structured_text") or block.get("text") or ""
        yield ("block", block["block_id"], text, block.get("section_id"),
               block.get("page"), block.get("bbox"), block.get("native_locator"))

        cur_sec = block.get("section_id")
        next_sec = blocks[idx + 1].get("section_id") if idx + 1 < len(blocks) else "__eof__"
        if cur_sec and cur_sec != next_sec and cur_sec in unanchored_by_sec:
            for tab in unanchored_by_sec.pop(cur_sec):
                if tab["table_id"] not in emitted_tables:
                    emitted_tables.add(tab["table_id"])
                    yield _emit_table(tab, cur_sec)

    for sec_key in list(unanchored_by_sec.keys()):
        for tab in unanchored_by_sec[sec_key]:
            if tab["table_id"] not in emitted_tables:
                emitted_tables.add(tab["table_id"])
                yield _emit_table(tab, None if sec_key == "__tail__" else sec_key)

    for table in doc.get("tables", []):
        if table["table_id"] not in emitted_tables:
            yield _emit_table(table, None)
    for figure in doc.get("figures", []):
        yield ("figure", figure["figure_id"], figure.get("caption") or "",
               figure.get("section_id"), figure.get("page"), figure.get("caption_bbox"), None)
    for eq in doc.get("formulas", {}).get("display_equations", []):
        yield ("eq", eq["eq_id"], eq.get("structured_text") or "",
               eq.get("section_id"), eq.get("page"), eq.get("bbox"), None)


def parse_anchor(anchor: str, default_paper: str = "paper_1") -> Tuple[str, str]:
    """Split optional 'paper_id:anchor_id'."""
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


def _join_continuation_text(a_text: str, b_text: str) -> str:
    a = (a_text or "").rstrip()
    b = (b_text or "").lstrip()
    if a.endswith("-") and b and b[0].islower():
        return a[:-1] + b
    return f"{a} {b}".strip()


class IndexStore:
    """Fast lookup wrapper over work/index.json entries, including continuation-block spans."""

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

    def continuation_pairs(self, pid: str) -> List[Tuple[Dict[str, Any], Dict[str, Any], str]]:
        pairs: List[Tuple[Dict[str, Any], Dict[str, Any], str]] = []
        items = [it for it in self.by_paper.get(pid, []) if it.get("kind") == "block"]
        for i, it in enumerate(items):
            nxt_anc = it.get("continued_by")
            nxt = self.get(pid, nxt_anc) if nxt_anc else (items[i + 1] if i + 1 < len(items) and items[i + 1].get("section_id") == it.get("section_id") else None)
            if nxt is not None:
                joined = normalize_text(_join_continuation_text(it.get("text", ""), nxt.get("text", "")))
                pairs.append((it, nxt, joined))
        return pairs


def _fuzzy_score(norm_q: str, norm_t: str) -> float:
    if not norm_q or not norm_t:
        return 0.0
    if norm_q in norm_t:
        return 1.0
    q_len = len(norm_q)
    if len(norm_t) <= q_len + 24:
        return difflib.SequenceMatcher(None, norm_q, norm_t).ratio()
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
    """Verify a verbatim quote against index entries (and continuation spans) and relocate if needed."""
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

    # Check cross-page / cross-column continuation pairs
    paper_order = [pid] + [op for op in store.by_paper.keys() if op != pid]
    for c_pid in paper_order:
        for first, second, joined in store.continuation_pairs(c_pid):
            if norm_q in joined:
                at_endpoint = target is not None and target["_idx"] in (first["_idx"], second["_idx"])
                chosen = target if at_endpoint else first
                return {
                    "match": "exact" if at_endpoint else "relocated",
                    "relocated_from": None if at_endpoint else (f"{pid}:{eid}" if anchor else None),
                    "paper_id": c_pid,
                    "anchor": chosen["anchor"],
                    "span_anchors": [first["anchor"], second["anchor"]],
                    "page": chosen.get("page"),
                    "section_id": chosen.get("section_id"),
                    "section_title": chosen.get("section_title"),
                    "block_text": _join_continuation_text(first.get("text", ""), second.get("text", "")),
                }

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


def _mag_factor(suf: str) -> float:
    low = suf.lower()
    if low in {"k", "thousand", "千"}:
        return 1e3
    if low == "万":
        return 1e4
    if low in {"m", "million", "百万"}:
        return 1e6
    if low == "亿":
        return 1e8
    if low in {"b", "billion", "十亿"}:
        return 1e9
    return 1.0


def _extract_numeric_tokens(text: str) -> List[str]:
    if not text:
        return []
    cleaned = _IGNORED_NUM_CTX_RE.sub(" ", text)
    nums: List[str] = []
    for m in _MAG_NUM_RE.finditer(cleaned):
        raw = m.group(1).replace(",", "")
        try:
            val = float(raw) * _mag_factor(m.group(2))
            nums.append(f"{val:g}")
        except ValueError:
            pass
    for m in _POW10_RE.finditer(cleaned):
        try:
            val = float(m.group(1).replace(",", "")) * (10.0 ** int(m.group(2)))
            nums.append(f"{val:g}")
        except ValueError:
            pass
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
    """Return numeric values present in text but missing from all evidence texts (with magnitude equivalence)."""
    if not text:
        return []
    ev_joined = " \n ".join(t for t in evidence_texts if t)
    ev_nums = set(_extract_numeric_tokens(ev_joined))
    ev_norm = normalize_text(ev_joined)
    cleaned = _IGNORED_NUM_CTX_RE.sub(" ", text)

    # Track spans covered by magnitude expressions (e.g. "100万" -> 1000000) so bare "100" isn't checked separately.
    mag_spans: List[Tuple[int, int]] = []
    missing: List[str] = []
    for m in _MAG_NUM_RE.finditer(cleaned):
        mag_spans.append(m.span())
        raw = m.group(0)
        try:
            val = float(m.group(1).replace(",", "")) * _mag_factor(m.group(2))
            canon = f"{val:g}"
            if canon not in ev_nums and m.group(1).replace(",", "") not in ev_norm:
                if raw not in missing:
                    missing.append(raw)
        except ValueError:
            pass

    for m in _NUM_RE.finditer(cleaned):
        if any(s <= m.start() and m.end() <= e for s, e in mag_spans):
            continue
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


def _canonical_unit(unit_raw: str) -> Tuple[str, str]:
    if unit_raw == "mW":
        return _UNIT_FAMILIES["mw_low"]
    if unit_raw == "MW":
        return _UNIT_FAMILIES["MW_up"]
    if unit_raw == "mM":
        return _UNIT_FAMILIES["mm_conc"]
    if unit_raw in {"μM", "uM"}:
        return _UNIT_FAMILIES["um_conc"]
    if unit_raw == "M":
        return _UNIT_FAMILIES["M_conc"]
    return _UNIT_FAMILIES.get(unit_raw.lower(), ("other", unit_raw))


def _extract_num_units(text: str) -> List[Tuple[str, str, str]]:
    out: List[Tuple[str, str, str]] = []
    for m in _UNIT_PATTERN.finditer(text or ""):
        try:
            num = f"{float(m.group(1).replace(',', '')):g}"
        except ValueError:
            continue
        fam, disp_unit = _canonical_unit(m.group(2))
        out.append((num, fam, disp_unit))
    return out


def check_units(text: str, evidence_texts: Iterable[str]) -> List[Dict[str, Any]]:
    """Flag same-number unit mismatches within a physical unit family (e.g. 12 Hz vs 12 kHz)."""
    if not text:
        return []
    ev_joined = " \n ".join(t for t in evidence_texts if t)
    ev_pairs = _extract_num_units(ev_joined)
    if not ev_pairs:
        return []
    ev_by_num_fam: Dict[Tuple[str, str], set[str]] = {}
    for num, fam, u in ev_pairs:
        ev_by_num_fam.setdefault((num, fam), set()).add(u)

    mismatches: List[Dict[str, Any]] = []
    seen = set()
    for num, fam, u in _extract_num_units(text):
        ev_units = ev_by_num_fam.get((num, fam))
        if ev_units and u not in ev_units and (num, fam, u) not in seen:
            seen.add((num, fam, u))
            mismatches.append({
                "number": num,
                "family": fam,
                "claim_unit": u,
                "evidence_units": sorted(ev_units),
            })
    return mismatches


def has_negation(text: str) -> bool:
    if not text:
        return False
    cleaned = _NEG_BASELINE_WITHOUT_RE.sub("baseline_ref", text)
    return bool(_NEG_EN_RE.search(cleaned) or _NEG_ZH_RE.search(cleaned))


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
    checks: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """Recursively verify quote-bearing objects inside answer.fields and sync page/anchor."""
    if isinstance(obj, list):
        for i, item in enumerate(obj):
            scan_fields_evidence(item, default_paper, store, multi_paper, change_records, warnings, f"{path}[{i}]", checks=checks)
        return
    if not isinstance(obj, dict):
        return

    quote_key = next((k for k in _QUOTE_KEYS if isinstance(obj.get(k), str) and obj[k].strip()), None)
    if quote_key is not None:
        anchor_key = next((k for k in _ANCHOR_KEYS if isinstance(obj.get(k), str) and obj[k].strip()), None)
        anchor_val = obj[anchor_key] if anchor_key else ""
        res = verify(obj[quote_key], anchor_val, default_paper, store)
        if checks is not None:
            checks.append({
                "context": path,
                "paper_id": res["paper_id"],
                "anchor": format_anchor(res["paper_id"], res["anchor"], multi_paper),
                "page": res.get("page"),
                "section_id": res.get("section_id"),
                "section_title": res.get("section_title"),
                "quote": obj[quote_key],
                "match": res["match"],
            })
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

    # If a dict pairs an answer/claim text field with embedded evidence quotes, audit numbers/units/negation.
    text_key = next((k for k in _TEXT_FIELD_KEYS if isinstance(obj.get(k), str) and obj[k].strip()), None)
    if text_key and (quote_key or isinstance(obj.get("evidence"), list)):
        ev_texts: List[str] = []
        if quote_key:
            ev_texts.append(obj[quote_key])
        if isinstance(obj.get("evidence"), list):
            for ev in obj["evidence"]:
                if isinstance(ev, dict):
                    qk = next((k for k in _QUOTE_KEYS if isinstance(ev.get(k), str) and ev[k].strip()), None)
                    if qk:
                        ev_texts.append(ev[qk])
                        ak = next((k for k in _ANCHOR_KEYS if isinstance(ev.get(k), str) and ev[k].strip()), None)
                        vr = verify(ev[qk], ev.get(ak, "") if ak else "", default_paper, store)
                        if vr.get("block_text"):
                            ev_texts.append(vr["block_text"])
        if ev_texts:
            um = check_units(obj[text_key], ev_texts)
            if um:
                warnings.append({"code": "unit_mismatch", "context": path, "mismatches": um})

    for k, v in obj.items():
        if isinstance(v, (dict, list)):
            scan_fields_evidence(v, default_paper, store, multi_paper, change_records, warnings, f"{path}.{k}", checks=checks)
