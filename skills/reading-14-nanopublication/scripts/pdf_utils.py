#!/usr/bin/env python3
"""Shared geometry, encoding, ligature, dehyphenation, and atomic I/O utilities."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

LIGATURE_MAP: Dict[str, str] = {
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl",
    "\ufb05": "st",
    "\ufb06": "st",
}

# Adobe Symbol / StandardSymL glyph slot mapping (only applied to explicit Symbol fonts)
SYMBOL_FONT_CHAR_MAP: Dict[str, str] = {
    "A": "Α",
    "B": "Β",
    "G": "Γ",
    "D": "Δ",
    "E": "Ε",
    "Z": "Ζ",
    "H": "Η",
    "Q": "Θ",
    "I": "Ι",
    "K": "Κ",
    "L": "Λ",
    "M": "Μ",
    "N": "Ν",
    "X": "Ξ",
    "O": "Ο",
    "P": "Π",
    "R": "Ρ",
    "S": "Σ",
    "T": "Τ",
    "U": "Υ",
    "F": "Φ",
    "C": "Χ",
    "Y": "Ψ",
    "W": "Ω",
    "a": "α",
    "b": "β",
    "g": "γ",
    "d": "δ",
    "e": "ε",
    "z": "ζ",
    "h": "η",
    "q": "θ",
    "i": "ι",
    "k": "κ",
    "l": "λ",
    "m": "μ",
    "n": "ν",
    "x": "ξ",
    "o": "ο",
    "p": "π",
    "r": "ρ",
    "V": "ς",
    "s": "σ",
    "t": "τ",
    "u": "υ",
    "f": "φ",
    "c": "χ",
    "y": "ψ",
    "w": "ω",
    "j": "φ",
    "v": "ϖ",
    "\xb0": "°",
    "\xb1": "±",
    "\xb2": "″",
    "\xb3": "≥",
    "\xb4": "×",
    "\xb5": "∝",
    "\xb6": "∂",
    "\xb7": "•",
    "\xb8": "÷",
    "\xb9": "≠",
    "\xba": "≡",
    "\xbb": "≈",
    "\xbc": "…",
    "\xa3": "≤",
    "\xa5": "∞",
    "\xa2": "′",
    "\xab": "↔",
    "\xac": "←",
    "\xad": "↑",
    "\xae": "→",
    "\xaf": "↓",
    "\xd6": "√",
    "\xd8": "¬",
    "\xd9": "∧",
    "\xda": "∨",
    "\xdb": "⇔",
    "\xdc": "⇐",
    "\xde": "⇒",
    "\xe1": "⟨",
    "\xf1": "⟩",
    "\xf2": "∫",
    "\xd1": "∇",
    "\xc6": "∅",
    "\xc7": "∩",
    "\xc8": "∪",
    "\xce": "∈",
    "\xcf": "∉",
    "\xca": "⊇",
    "\xc9": "⊃",
    "\xcb": "⊄",
    "\xcc": "⊂",
    "\xcd": "⊆",
    "\xc5": "⊕",
    "\xc4": "⊗",
    "\x24": "∃",
    "\x22": "∀",
}

COMPOUND_HYPHEN_PREFIXES = {
    "self",
    "non",
    "co",
    "pre",
    "post",
    "multi",
    "cross",
    "well",
    "high",
    "low",
    "two",
    "three",
    "four",
    "five",
    "sub",
    "anti",
    "semi",
    "inter",
    "intra",
    "re",
    "over",
    "under",
    "micro",
    "macro",
    "nano",
    "meta",
    "quasi",
    "pseudo",
    "ultra",
    "hyper",
    "super",
    "counter",
    "mid",
    "full",
    "half",
    "single",
    "double",
    "multi",
    "solid",
    "gas",
    "liquid",
    "long",
    "short",
    "real",
    "open",
    "closed",
    "state",
    "x",
    "p",
    "n",
}

CID_PATTERN = re.compile(r"\(cid:\d+\)")


def probe_capabilities() -> Dict[str, Any]:
    """Detect available Python PDF/OCR packages and system CLI binaries."""
    py_pkgs = {}
    for pkg in ("fitz", "pdfplumber", "pypdf", "PIL", "numpy", "pytesseract"):
        py_pkgs[pkg] = importlib.util.find_spec(pkg) is not None

    cli_tools: Dict[str, Any] = {}
    for tool in ("pdftotext", "pdfinfo", "pdftoppm", "tesseract"):
        path = shutil.which(tool)
        cli_tools[tool] = {"available": path is not None, "path": path}

    tesseract_langs: List[str] = []
    if cli_tools["tesseract"]["available"]:
        try:
            proc = subprocess.run(
                ["tesseract", "--list-langs"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
            tesseract_langs = [ln for ln in lines if not ln.lower().startswith("list of available")]
        except Exception:
            tesseract_langs = []

    return {
        "python_packages": py_pkgs,
        "cli_tools": cli_tools,
        "tesseract_langs": tesseract_langs,
    }


def round_bbox(bbox: Sequence[float], ndigits: int = 2) -> List[float]:
    """Round a 4-element bounding box [x0, y0, x1, y1]."""
    return [round(float(v), ndigits) for v in bbox[:4]]


def bbox_area(bbox: Sequence[float]) -> float:
    """Compute area of [x0, y0, x1, y1]."""
    w = max(0.0, float(bbox[2]) - float(bbox[0]))
    h = max(0.0, float(bbox[3]) - float(bbox[1]))
    return w * h


def bbox_intersection_area(a: Sequence[float], b: Sequence[float]) -> float:
    """Compute intersection area of two bounding boxes."""
    x0 = max(float(a[0]), float(b[0]))
    y0 = max(float(a[1]), float(b[1]))
    x1 = min(float(a[2]), float(b[2]))
    y1 = min(float(a[3]), float(b[3]))
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0)


def bbox_overlap_ratio(inner: Sequence[float], outer: Sequence[float]) -> float:
    """Fraction of `inner` area covered by `outer`."""
    area = bbox_area(inner)
    if area <= 0.0:
        return 0.0
    return bbox_intersection_area(inner, outer) / area


def bbox_iou(a: Sequence[float], b: Sequence[float]) -> float:
    """Intersection-over-Union of two bounding boxes."""
    inter = bbox_intersection_area(a, b)
    if inter <= 0.0:
        return 0.0
    union = bbox_area(a) + bbox_area(b) - inter
    return inter / union if union > 0.0 else 0.0


def bbox_union(boxes: Iterable[Sequence[float]]) -> List[float]:
    """Return enclosing bounding box of non-empty sequence of boxes."""
    box_list = list(boxes)
    if not box_list:
        return [0.0, 0.0, 0.0, 0.0]
    return [
        round(min(float(b[0]) for b in box_list), 2),
        round(min(float(b[1]) for b in box_list), 2),
        round(max(float(b[2]) for b in box_list), 2),
        round(max(float(b[3]) for b in box_list), 2),
    ]


def normalize_ligatures(text: str) -> Tuple[str, int]:
    """Replace Unicode typographic ligatures with ASCII sequences."""
    count = 0
    out = text
    for src, dst in LIGATURE_MAP.items():
        if src in out:
            n = out.count(src)
            out = out.replace(src, dst)
            count += n
    return out, count


def is_symbol_font(font_name: Optional[str]) -> bool:
    """Return True if font name refers to an Adobe Symbol / StandardSymL legacy encoding font."""
    if not font_name:
        return False
    low = font_name.lower()
    if "cambriamath" in low or "stix" in low or "symbola" in low:
        return False
    return (
        "standardsym" in low
        or low == "symbol"
        or low.endswith("+symbol")
        or "-symbol" in low
        or "symbolmt" in low
        or re.search(r"(^|[+\-_])symbol($|[+\-_])", low) is not None
    )


def decode_symbol_font_text(text: str) -> Tuple[str, int]:
    """Map legacy Symbol font character slots to Unicode Greek and math symbols."""
    mapped = []
    changes = 0
    for ch in text:
        repl = SYMBOL_FONT_CHAR_MAP.get(ch)
        if repl is not None:
            mapped.append(repl)
            changes += 1
        else:
            mapped.append(ch)
    return "".join(mapped), changes


def detect_garbled_metrics(text: str) -> Dict[str, Any]:
    """Compute replacement-char, Private Use Area, CID, and control-char diagnostics."""
    n = len(text)
    if n == 0:
        return {
            "char_count": 0,
            "replacement_count": 0,
            "replacement_ratio": 0.0,
            "pua_count": 0,
            "pua_ratio": 0.0,
            "cid_count": 0,
            "control_count": 0,
            "control_ratio": 0.0,
            "is_garbled": False,
            "reasons": [],
        }

    replacement_count = text.count("\ufffd")
    pua_count = sum(1 for ch in text if 0xE000 <= ord(ch) <= 0xF8FF)
    cid_matches = CID_PATTERN.findall(text)
    cid_count = len(cid_matches)
    control_count = sum(
        1 for ch in text if ord(ch) < 32 and ch not in ("\n", "\r", "\t")
    )

    replacement_ratio = replacement_count / n
    pua_ratio = pua_count / n
    control_ratio = control_count / n

    reasons: List[str] = []
    if replacement_ratio > 0.02 or replacement_count >= 5:
        reasons.append(f"replacement_chars={replacement_count} ({replacement_ratio:.2%})")
    if pua_ratio > 0.03 or pua_count >= 8:
        reasons.append(f"pua_chars={pua_count} ({pua_ratio:.2%})")
    if cid_count >= 3:
        reasons.append(f"cid_tokens={cid_count}")
    if control_ratio > 0.02:
        reasons.append(f"control_chars={control_count} ({control_ratio:.2%})")

    return {
        "char_count": n,
        "replacement_count": replacement_count,
        "replacement_ratio": round(replacement_ratio, 4),
        "pua_count": pua_count,
        "pua_ratio": round(pua_ratio, 4),
        "cid_count": cid_count,
        "control_count": control_count,
        "control_ratio": round(control_ratio, 4),
        "is_garbled": len(reasons) > 0,
        "reasons": reasons,
    }


def dehyphenate_lines(lines: Sequence[str]) -> Tuple[str, List[Dict[str, str]]]:
    """Join soft line-end hyphens across lines while preserving compound prefixes."""
    cleaned_lines = [ln.strip() for ln in lines if ln and ln.strip()]
    if not cleaned_lines:
        return "", []

    changes: List[Dict[str, str]] = []
    out_parts: List[str] = [cleaned_lines[0]]

    for nxt in cleaned_lines[1:]:
        prev = out_parts[-1]
        m_prev = re.search(r"([A-Za-z]+)-$", prev)
        m_next = re.match(r"^([a-z][A-Za-z]*)(.*)$", nxt)
        if m_prev and m_next:
            prefix = m_prev.group(1)
            suffix = m_next.group(1)
            rest = m_next.group(2)
            if prefix.lower() in COMPOUND_HYPHEN_PREFIXES:
                out_parts[-1] = prev + nxt
                changes.append(
                    {
                        "type": "compound_hyphen_kept",
                        "before": f"{prefix}- {suffix}",
                        "after": f"{prefix}-{suffix}",
                    }
                )
            else:
                merged_word = prefix + suffix
                out_parts[-1] = prev[: m_prev.start()] + merged_word + rest
                changes.append(
                    {
                        "type": "dehyphenation",
                        "before": f"{prefix}- {suffix}",
                        "after": merged_word,
                    }
                )
        else:
            # Avoid extra space between CJK characters
            if re.search(r"[\u4e00-\u9fff]$", prev) and re.match(r"^[\u4e00-\u9fff]", nxt):
                out_parts.append(nxt)
                out_parts[-2:] = [out_parts[-2] + out_parts[-1]]
            else:
                out_parts.append(nxt)

    return " ".join(out_parts), changes


def write_text_lf(path: Path | str, text: str) -> None:
    """Write UTF-8 text atomically using LF line endings."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp_", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(normalized)
        os.replace(tmp_name, target)
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def write_json_atomic(path: Path | str, data: Any, indent: int = 2) -> None:
    """Write UTF-8 JSON atomically using LF line endings."""
    content = json.dumps(data, ensure_ascii=False, indent=indent) + "\n"
    write_text_lf(path, content)
