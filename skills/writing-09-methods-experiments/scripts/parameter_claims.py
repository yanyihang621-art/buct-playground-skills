"""Check parameter assignments in prose against the source table, without executing inputs."""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, DecimalException, InvalidOperation


NUMBER = r"[+−-]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?"
TOKEN = re.compile(rf"(?<![A-Za-z0-9_.]){NUMBER}(?![0-9]|\.[0-9])")
ALIASES = {
    "learning_rate": ("learning rate", "lr", "学习率"),
    "batch_size": ("batch size", "批大小", "批量大小"),
    "max_epochs": ("maximum epochs", "max epochs", "最大训练轮数"),
    "seed": ("random seed", "随机种子"),
    "warmup_steps": ("warmup steps", "warm-up steps", "预热步数"),
    "dropout": ("dropout rate", "丢弃率"),
    "epochs": ("epochs", "训练轮数"),
}
LINK = re.compile(
    r"^\s*(?:(?:[=:|]|为每批|为|取为|设为|设置为|取值为|是|取|使用)\s*|"
    r"(?:(?:is|was|were|of|at|equals?|set to|used|fixed at)\s+){1,3})?",
    re.IGNORECASE,
)
# Conversion is only used when the source and manuscript both state recognized units.
UNITS = {
    "k": ("temperature", "1", "0"), "°c": ("temperature", "1", "273.15"),
    "pa": ("pressure", "1", "0"), "kpa": ("pressure", "1000", "0"),
    "mpa": ("pressure", "1000000", "0"), "bar": ("pressure", "100000", "0"),
    "s": ("time", "1", "0"), "ms": ("time", ".001", "0"),
    "min": ("time", "60", "0"), "h": ("time", "3600", "0"),
    "g": ("mass", "1", "0"), "mg": ("mass", ".001", "0"), "kg": ("mass", "1000", "0"),
    "l": ("volume", "1", "0"), "ml": ("volume", ".001", "0"), "μl": ("volume", ".000001", "0"),
    "m": ("length", "1", "0"), "cm": ("length", ".01", "0"), "mm": ("length", ".001", "0"),
    "μm": ("length", ".000001", "0"), "nm": ("length", ".000000001", "0"), "%": ("percent", "1", "0"),
}
UNIT_ALIASES = {
    "s": ("second", "seconds", "sec", "秒"), "ms": ("milliseconds", "毫秒"),
    "min": ("minutes", "minute", "分钟"), "h": ("hour", "hours", "小时"),
    "k": ("kelvin", "kelvins"), "°c": ("摄氏度",), "g": ("grams", "gram", "克"),
    "ml": ("milliliters", "毫升"), "kg": ("kilograms", "千克"),
}
for base, alternates in UNIT_ALIASES.items():
    for spelling in alternates:
        UNITS[spelling] = UNITS[base]
for dimension, labels in {
        "samples": ("sample", "samples", "个样本", "样本"),
        "epochs": ("epoch", "epochs", "轮"), "steps": ("steps", "step", "步"),
        "pixels": ("pixel", "pixels", "px", "像素"),
        "repeats": ("repeats", "replicates", "次重复"),
        "frequency": ("hz", "赫兹"), "rotation": ("rpm",)}.items():
    for label in labels:
        UNITS[label] = (dimension, "1", "0")
UNIT_AFTER = re.compile(r"^\s*(" + "|".join(sorted(UNITS, key=len, reverse=True)) + r")(?![a-zA-Z/])", re.I)


def plain(text):
    text = str(text).replace("−", "-").replace("µ", "μ")
    text = re.sub(r"(\d(?:\.\d+)?)\s*[×·]\s*10\s*(?:\^\s*)?([⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]+|\^?[+-]?\d+)",
                  lambda m: m[1] + "e" + unicodedata.normalize("NFKC", m[2]).replace("−", "-").lstrip("^"), text)
    return unicodedata.normalize("NFKC", text).replace(chr(96), "").replace("**", "")


def decimal(value):
    text = str(value).strip().replace("−", "-")
    if not re.fullmatch(NUMBER, text):
        return None
    try:
        result = Decimal(text.replace(",", ""))
        return result if result.is_finite() else None
    except InvalidOperation:
        return None


def same_number(expected, actual, unit="", actual_unit="", fractional=False):
    left, right = decimal(expected), decimal(actual)
    if left is None or right is None:
        return False
    src, dst = UNITS.get(unit.casefold()), UNITS.get(actual_unit.casefold())
    if fractional and not src and actual_unit == "%" and abs(left) <= 1:
        return right.scaleb(-2) == left
    if src and dst and src != dst:
        if src[0] != dst[0]:
            return False
        try:
            left = left * Decimal(src[1]) + Decimal(src[2])
            right = right * Decimal(dst[1]) + Decimal(dst[2])
        except DecimalException:
            return False
    return left == right


def value_present(value, text):
    if decimal(value) is not None:
        return any(same_number(value, match[0]) for match in TOKEN.finditer(plain(text)))
    return bool(re.search(r"(?<![A-Za-z0-9_])" + re.escape(plain(value)) + r"(?![A-Za-z0-9_])", plain(text), re.I))


def name_pattern(name, aliases=()):
    names = {name, name.replace("_", " "), *ALIASES.get(name.lower(), ()), *aliases}
    parts = [re.escape(word) for word in sorted(names, key=len, reverse=True) if word]
    return re.compile(r"(?<![A-Za-z0-9_.])(?:" + "|".join(parts) + r")(?![A-Za-z0-9_])", re.I)


def inspect_assignments(chapter, params, facts):
    lines = plain(chapter).splitlines()
    sources = set(facts.get("scanned_sources", []))
    evidence = {(str(e.get("source", "")), str(e.get("locator", ""))) for e in facts.get("evidence", [])}
    conflicts = {c.get("parameter"): c.get("values", {}) for c in facts.get("conflicts", [])}
    errors, review, matched = [], [], 0
    missing = 0
    # A short alias shared by multiple scoped rows would join different runs or phases.
    leaf_counts = {}
    for row in params:
        leaf = str(row.get("name", "")).rsplit(".", 1)[-1]
        leaf_counts[leaf] = leaf_counts.get(leaf, 0) + 1
    for row in params:
        name = str(row.get("name", "")).strip()
        value = row.get("value")
        source = str(row.get("source") or "")
        locator = str(row.get("locator") or "")
        unresolved = row.get("status") == "unresolved" or (value is None and name in conflicts)
        if not name or (value is None and not unresolved):
            errors.append({"parameter": name, "reason": "parameter_name_or_value_missing"})
            continue
        if (not source or (sources and source not in sources) or
                (locator and evidence and (source, locator) not in evidence)):
            errors.append({"parameter": name, "reason": "untraceable_parameter_source", "source": source, "locator": locator})
        leaf = name.rsplit(".", 1)[-1]
        aliases = [leaf, leaf.replace("_", " ")] if leaf != name and leaf_counts[leaf] == 1 else []
        pattern = name_pattern(name, aliases)
        pairs = []
        mentioned = False
        candidates = list(conflicts.get(name, {}).values()) if unresolved else [value]
        if not candidates:
            errors.append({"parameter": name, "reason": "unresolved_parameter_without_alternatives"})
            continue
        numeric = all(decimal(v) is not None for v in candidates)
        source_unit = str(row.get("unit") or "")
        fractional = bool(re.search(r"dropout|accuracy|fraction|proportion|macro_f1|recall|precision|specificity", name, re.I))
        for idx, line in enumerate(lines, start=1):
            for label in pattern.finditer(line):
                mentioned = True
                tail = line[label.end():].lstrip()
                link = LINK.match(tail)
                rhs = tail[link.end():].lstrip("'\"")
                if numeric:
                    token = TOKEN.match(rhs)
                    if token:
                        unit = UNIT_AFTER.match(rhs[token.end():])
                        actual_unit = unit[1] if unit else ""
                        good = any(same_number(v, token[0], source_unit, actual_unit, fractional) for v in candidates)
                        pairs.append((idx, token[0] + actual_unit, good))
                elif any(value_present(v, rhs[:len(str(v)) + 3]) for v in candidates):
                    pairs.append((idx, rhs[:40], True))
                elif re.match(r"^[=:|]", tail) and (word := re.match(r"[\w./+-]+", rhs)):
                    pairs.append((idx, word[0], False))
        if unresolved:
            # Alternatives have no single value to include in the match ratio.
            pass
        elif any(good for _, _, good in pairs):
            matched += 1
        else:
            missing += 1
            if not pairs:
                review.append({"parameter": name, "reason": "check_paraphrase_or_attachment" if mentioned else "table_only",
                               "source": source, "locator": locator})
        for idx, observed, good in pairs:
            if not good:
                errors.append({"parameter": name, "expected": candidates if unresolved else value, "observed": observed, "line": idx,
                               "source": source, "locator": locator, "reason": "inconsistent_local_assignment"})
    total = matched + missing
    return errors, review, {"matched_parameter_count": matched, "comparable_parameter_count": total,
                            "parameter_prose_consistency": round(matched / total, 4) if total else None}
