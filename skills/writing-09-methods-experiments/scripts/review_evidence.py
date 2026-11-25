"""Join a case-authored manuscript to indexed source passages and review annotations."""

from collections import defaultdict
from copy import deepcopy
from decimal import Decimal
import re
import unicodedata

from parameter_claims import TOKEN, UNIT_AFTER, UNITS, plain, same_number


def normalized(value):
    return " ".join(unicodedata.normalize("NFKC", str(value)).split())


# Check numeric literals with their signs and scientific notation. The semantic
# meaning (scope, direction and units) still requires reading the linked passage.
NUMBER = re.compile(r"(?<![\w.])[+−-]?(?:\d+(?:[.,]\d+)*|\.\d+)(?:[eE][+\-]?\d+)?(?![0-9])", re.ASCII)


def numbers(text):
    values = set()
    for token in NUMBER.findall(normalized(text)):
        token = token.replace("−", "-")
        if re.fullmatch(r"[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?", token):
            token = token.replace(",", "")
        try:
            values.add(Decimal(token))
        except ArithmeticError:
            values.add(token)
    return values


def converted_numbers(claim, quotes):
    """Accept documented unit transformations only for matched physical dimensions."""
    def measurements(text):
        text = plain(text)
        for number in TOKEN.finditer(text):
            unit = UNIT_AFTER.match(text[number.end():])
            if unit:
                yield number[0], unit[1].casefold()

    original = list(measurements(quotes))
    justified = set()
    for value, unit in measurements(claim):
        for source_value, source_unit in original:
            if (UNITS[unit][0] == UNITS[source_unit][0]
                    and same_number(source_value, value, source_unit, unit)):
                justified.update(numbers(value))
                break
    return justified


def strings(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)
    elif value is not None:
        yield str(value)


def add_annotation(facts, table, annotation, link, quotes):
    section = annotation.get("section")
    value = annotation.get("value")
    if section not in {"steps", "setup", "datasets", "baselines", "limitations", "parameters"}:
        return "unknown_fact_section"
    if value in (None, "", [], {}) or any(numbers(x) - numbers(quotes) for x in strings(value)):
        return "empty_or_unsubstantiated_fact"
    # Store extracted field values verbatim (translations and synthesis belong
    # in the manuscript). Several locators may jointly support the same claim.
    if any(normalized(x).casefold() not in quotes.casefold() for x in strings(value)):
        return "fact_value_not_in_excerpt"
    if section == "setup":
        key = annotation.get("field")
        if key not in {"software", "hardware", "seeds", "repeats", "command", "train_protocol", "val_protocol", "test_protocol", "code_version", "data_access"}:
            return "unknown_setup_field"
        existing = facts["setup"].get(key)
        if existing not in (None, "", []) and existing != value:
            return "conflicts_with_existing_setup"
        facts["setup"][key] = value
    elif section == "limitations" and isinstance(value, str):
        if value not in facts[section]:
            facts[section].append(value)
    elif section in {"steps", "baselines", "datasets", "parameters"} and isinstance(value, dict) and value.get("name"):
        if section == "parameters" and value.get("value") is None:
            return "missing_parameter_value"
        rows = table["parameters"] if section == "parameters" else facts[section]
        existing = next((row for row in rows if isinstance(row, dict) and row.get("name") == value["name"]), None)
        if existing is not None:
            if any(k in existing and existing[k] not in (None, "", []) and str(existing[k]) != str(v) for k, v in value.items()):
                return "conflicts_with_existing_row"
            if existing.get("status") == "unresolved":
                return "unresolved_parameter"
            for key, item in value.items():
                if existing.get(key) in (None, "", []):
                    existing[key] = item
        else:
            new = dict(value, source=link["source"], locator=link["locator"])
            if section == "parameters":
                new.setdefault("unit", "")
                new.setdefault("target_component", "")
            rows.append(new)
    else:
        return "invalid_fact_value"
    return None


def apply_review(facts, param_table, review, chapter_md):
    facts, table = deepcopy(facts), deepcopy(param_table)
    issues, valid = [], []
    evidence = defaultdict(list)
    for row in facts.get("evidence", []):
        evidence[(row["source"], row["locator"])].append(normalized(row["text"]))
    groups = defaultdict(list)
    claims = review.get("claim_sources", []) if isinstance(review, dict) else None
    if not isinstance(claims, list):
        return facts, table, [], [{"code": "invalid_claim_sources"}]
    manuscript = normalized(chapter_md)
    for i, entry in enumerate(claims):
        if not isinstance(entry, dict) or not all(isinstance(entry.get(k), str) and entry[k].strip() for k in ("claim", "source", "locator", "quote")):
            issues.append({"code": "incomplete_claim_link", "row": i})
            continue
        claim, quote = normalized(entry["claim"]), normalized(entry["quote"])
        if claim not in manuscript:
            issues.append({"code": "claim_absent_from_manuscript", "row": i, "claim": claim})
        elif not any(quote in text for text in evidence[(entry["source"], entry["locator"])]):
            issues.append({"code": "quote_or_locator_not_found", "row": i, "source": entry["source"], "locator": entry["locator"]})
        else:
            groups[claim].append(entry)

    for claim, entries in groups.items():
        quotes = " ".join(normalized(entry["quote"]) for entry in entries)
        unsupported = numbers(claim) - numbers(quotes)
        if unsupported:
            unsupported -= converted_numbers(claim, quotes)
        if unsupported:
            issues.append({"code": "unsupported_claim_numbers", "claim": claim, "numbers": sorted(map(str, unsupported))})
            continue
        for entry in entries:
            link = {key: entry[key] for key in ("claim", "source", "locator", "quote")}
            valid.append(link)
            if "fact" in entry:
                annotation = entry["fact"]
                reason = add_annotation(facts, table, annotation, link, quotes) if isinstance(annotation, dict) else "invalid_fact"
                if reason:
                    issues.append({"code": reason, "claim": claim, "source": link["source"]})
    if claims:
        # Every quantitative statement needs its own source. A number found in
        # an unrelated paragraph or merely somewhere in the paper cannot cover it.
        supported = sorted({normalized(link["claim"]) for link in valid}, key=len, reverse=True)
        for line_no, line in enumerate(chapter_md.splitlines(), 1):
            line = normalized(line).strip()
            if not line or line.startswith(("#", "|---", "```")):
                continue
            rest = re.sub(r"^\s*(?:[-*]|\d+[.)、])\s+", "", line)
            for fragment in supported:
                rest = rest.replace(fragment, " ")
            rest = re.sub(r"\[(?:[A-Za-z]?\d+(?:[-–, ]+[A-Za-z]?\d+)*)\]", "", rest)
            if numbers(rest):
                issues.append({"code": "unmapped_manuscript_numbers", "line": line_no, "excerpt": rest[:220]})
    table["parameter_count"] = len(table["parameters"])
    previous_gaps = set(facts.get("missing_repro_fields", []))
    resolved = {key for key in previous_gaps if facts["setup"].get(key) not in (None, "", [])}
    if facts.get("datasets") and all(isinstance(row, dict) and row.get("version") for row in facts["datasets"]):
        resolved.add("dataset_version")
    facts["missing_repro_fields"] = [key for key in facts.get("missing_repro_fields", []) if key not in resolved]
    facts["author_questions"] = [q for q in facts.get("author_questions", []) if q.get("field") not in resolved or q.get("field") not in previous_gaps]
    return facts, table, valid, issues
