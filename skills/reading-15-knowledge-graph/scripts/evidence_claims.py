"""Read locally defined abbreviations and review graph claims against their source paragraphs."""

import re
import unicodedata

ABBREVIATION = re.compile(r"([A-Za-z][A-Za-z0-9 -]{2,100})\s*\(([A-Z][A-Za-z0-9-]{1,12})\)")


# Only peel off complete measurement contexts and clearly verbal purpose phrases.
QUANTITY = (r"[+-]?\d+(?:\.\d+)?(?:\s*[-–]\s*\d+(?:\.\d+)?)?\s*"
            r"(?:nm|[μµu]m|mm|cm|mV|kV|V|mA|A|kHz|MHz|GHz|Hz|mW|W|K|°\s*C|MPa|kPa|Pa|bar|rpm|%)")
MEASUREMENT_CONTEXT = r"(?:" + QUANTITY + r"|room temperature|ambient (?:temperature|pressure)|atmospheric pressure)"
MEASURED_AT = re.compile(r"\s+(at\s+" + MEASUREMENT_CONTEXT + r"(?:\s+(?:and|or)\s+" + MEASUREMENT_CONTEXT + r")*)$", re.I)
PURPOSE = re.compile(
    r"\s+((?:in order )?to\s+(?:test|measure|record|monitor|detect|quantify|characteri[sz]e|calibrate|assess|"
    r"estimate|determine|collect|capture|compare|evaluate|verify)\s+\S.*)$", re.I)
NOUN_COMPLEMENT = re.compile(r"\b(?:resistance|response|access|exposure|sensitivity|susceptibility|"
                             r"approach|ability|capacity|capability)\s*$", re.I)


def split_object_context(label, relation):
    """Separate the object mention, measurement context and action purpose without paraphrasing."""
    condition = None
    purpose = None
    if relation == "uses":
        match = PURPOSE.search(label)
        if match and not NOUN_COMPLEMENT.search(label[:match.start()]):
            purpose = match[1]
            label = label[:match.start()].rstrip()
    if relation in {"increases", "decreases", "inhibits", "achieves", "outperforms", "uses"}:
        match = MEASURED_AT.search(label)
        if match:
            condition = match[1]
            label = label[:match.start()].rstrip()
    return label, condition, purpose


def canon(label):
    # Greek letters and optical/electrical signs distinguish real scientific terms.
    # Keep them intact; normalize hyphenation between words only.
    text = unicodedata.normalize("NFC", label).casefold()
    text = re.sub(r"^(?:the|an?)\s+(?=\w)", "", text)
    text = re.sub(r"(?<=[^\W\d_])[-‐‑](?=[^\W\d_])", " ", text)
    return re.sub(r"[^\w+−%°±⁺⁻/\-]+", " ", text).strip()


def _long_form(prefix, short):
    """Find the shortest suffix whose letters spell the parenthetical short form."""
    remaining = prefix.lower()
    chars = [ch.lower() for ch in short if ch.isalnum()]
    pos = len(prefix)
    for i in reversed(range(len(chars))):
        while True:
            pos = remaining.rfind(chars[i], 0, pos)
            if pos < 0:
                return None
            if i or pos == 0 or not remaining[pos - 1].isalnum():
                break
    phrase = prefix[pos:].strip()
    if len(phrase.split()) <= min(2 * len(chars), len(chars) + 5):
        return phrase
    return None


def read_definitions(papers, base):
    rows = {}
    for definition in (base or {}).get("abbreviation_definitions", []):
        rows[(definition["source_id"], definition["paragraph_id"], definition["short"], definition["full"])] = definition
    for paper in papers:
        sid = paper["source_id"]
        for para in paper.get("paragraphs", []):
            for match in ABBREVIATION.finditer(para["text"]):
                short = match[2]
                full = _long_form(match[1], short)
                if not full:
                    continue
                key = (sid, para["paragraph_id"], short, full)
                start = match.start(1) + match[1].rfind(full)
                rows[key] = {
                    "short": short, "full": full, "source_id": sid,
                    "paragraph_id": para["paragraph_id"], "location_type": para["location_type"],
                    "pdf_page": para.get("pdf_page"), "quote_snippet": para["text"][start:match.end()],
                }
    return [rows[k] for k in sorted(rows)]


def local_aliases(definitions):
    per_source = {}
    forms = {}
    for row in definitions:
        key = canon(row["short"])
        per_source.setdefault(row["source_id"], {}).setdefault(key, set()).add(canon(row["full"]))
        forms.setdefault(key, set()).add(canon(row["full"]))
    aliases = {}
    for sid, local in per_source.items():
        aliases[sid] = {key: next(iter(values)) for key, values in local.items() if len(values) == 1}
    return aliases, forms


def read_reviewed_claims(review, papers, ontology):
    """Replacements are per paragraph. Reject unknown provenance or unquoted entities."""
    if review is None:
        return {}
    if not isinstance(review, dict) or not isinstance(review.get("paragraphs"), list):
        raise ValueError("Expected an object containing a paragraphs array.")
    if review.get("mode", "replace_paragraphs") not in {"reviewed_only", "replace_paragraphs"}:
        raise ValueError("Unknown claims mode. Use reviewed_only or replace_paragraphs.")
    paragraphs = {(p["source_id"], para["paragraph_id"]): para
                  for p in papers for para in p.get("paragraphs", [])}
    entity_types = set(ontology["entity_types"])
    relation_types = {rel["id"] for rel in ontology["relation_types"]} - {"instance_of"}
    strength_types = set(ontology["evidence_strength_tiers"]) - {"direct_tabular_evidence"}
    verified = {}
    for entry in review["paragraphs"]:
        key = (entry["source_id"], entry["paragraph_id"])
        if key not in paragraphs or key in verified or not isinstance(entry.get("claims"), list):
            raise ValueError(f"Missing or repeated paragraph or malformed claims at {key}.")
        para = paragraphs[key]
        checked = []
        for claim in entry["claims"]:
            quote = claim["quote_snippet"].strip()
            subject = claim["subject"].strip()
            obj = claim["object"].strip()
            strength = claim["evidence_strength"]
            conditions = claim.get("conditions", [])
            purpose = claim.get("purpose")
            if purpose is not None and (not isinstance(purpose, str) or not purpose.strip() or purpose not in quote):
                raise ValueError(f"Purpose must be a literal span in the cited quote at {key}.")
            if not quote or quote not in para["text"] or not subject or not obj:
                raise ValueError(f"Expected a verbatim quote and both endpoint mentions at {key}.")
            if subject.casefold() not in quote.casefold() or obj.casefold() not in quote.casefold():
                raise ValueError(f"Both endpoint mentions must occur in the quote at {key}.")
            if not isinstance(conditions, list) or any(not isinstance(c, str) or not c or c not in para["text"] for c in conditions):
                raise ValueError(f"Unverified condition at {key}.")
            if claim["relation"] not in relation_types or strength not in strength_types:
                raise ValueError(f"Unknown relation or evidence tier at {key}.")
            if claim["subject_type"] not in entity_types or claim["object_type"] not in entity_types:
                raise ValueError(f"Unknown entity type at {key}.")
            checked.append({
                "subj_raw": subject, "subj_type": claim["subject_type"],
                "obj_raw": obj, "obj_type": claim["object_type"], "rel_type": claim["relation"],
                "ev_strength": strength, "conds": conditions, "purpose": purpose,
                "anchor": {"source_id": key[0], "paragraph_id": key[1], "quote_snippet": quote,
                           "location_type": para["location_type"], "pdf_page": para.get("pdf_page")},
            })
        verified[key] = checked
    return verified
