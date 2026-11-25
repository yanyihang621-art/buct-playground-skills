"""Join editorial claims to their source passages, checking identity and study ownership."""
from collections import defaultdict
from copy import deepcopy
import re
import unicodedata


def normalized(text):
    return " ".join(unicodedata.normalize("NFKC", str(text)).split())


def _fold(text):
    return re.sub(r"[^\w]", "", normalized(text).casefold()).replace("_", "")


def _doi(text):
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", normalized(text).casefold())


def _present(value):
    return isinstance(value, str) and bool(value.strip())


def _rows(value):
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _owners(passages, papers, annotations):
    """Direct record membership is definitive. Use a document title/DOI only for prose sources."""
    by_id, by_path = defaultdict(set), defaultdict(set)
    for paper in papers.values():
        for sid in paper.get("source_ids") or []:
            if isinstance(sid, str) and sid in passages:
                by_id[sid].add(paper["cite_key"])
    known = [(k, v) for k, v in papers.items()]
    known.extend((row["cite_key"], row) for row in annotations
                 if _present(row.get("cite_key")) and row["cite_key"] not in papers)
    identifiers = [(key, _fold(row.get("title") or ""), _doi(row.get("doi") or "")) for key, row in known]
    # Multiple titles in a compiled source are ambiguous; never assign it solely to its first entry.
    for passage in passages.values():
        path = passage["path"]
        if not path.lower().endswith((".md", ".markdown", ".txt", ".pdf", ".docx", ".tex")):
            continue
        header = passage["text"][:700]
        folded = _fold(header)
        for key, title, doi in identifiers:
            if len(title) >= 6 and title in folded or doi and doi in header.casefold():
                by_path[path].add(key)
    return by_id, by_path


META_LOCATOR = re.compile(r"/(?:title|authors?|year|venue|journal|doi|cite_key|id|issued|container-title)(?:/|$)", re.I)
QUANTITY = re.compile(r"(?<![\w.])([−+-]?\d+(?:\.\d+)?)\s*(%|％|mmol|μmol|μm|µm|nm|mm|cm|kg|mg|mL|MPa|kPa|GHz|MHz|kHz|Hz|ms|°C|℃|ppm|K)(?![A-Za-z])")


def _quantities(text):
    groups = defaultdict(set)
    for value, unit in QUANTITY.findall(normalized(text)):
        groups[unit].add(float(value.replace("−", "-")))
    return groups


def apply_editorial_evidence(materials, analysis):
    """Return enriched materials, validated notes, and errors requiring a revision."""
    enriched, issues = deepcopy(materials), []
    if not isinstance(analysis, dict):
        return enriched, {}, [{"code": "invalid_editorial_analysis", "message": "Supply an analysis JSON object."}]
    notes = deepcopy(analysis)
    for key in ("uncertainties", "change_records"):
        if not isinstance(notes.get(key, []), list):
            issues.append({"code": "expected_array", "field": key})
            notes[key] = []
    reviews = notes.setdefault("uncertainties", [])

    def report(code, row, message, *, review=False, **data):
        item = {"code": code, "row": row, "message": message, **data}
        if item not in (reviews if review else issues):
            (reviews if review else issues).append(item)

    def read_rows(name):
        rows = notes.get(name, [])
        if not isinstance(rows, list):
            report("expected_array", name, "Supply an array of JSON objects.")
            return []
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                report("expected_object", f"{name}[{i}]", "Supply a JSON object for this row.")
        return [(i, row) for i, row in enumerate(rows) if isinstance(row, dict)]

    passages = {p["source_id"]: p for p in _rows(materials.get("passages")) if _present(p.get("source_id"))}
    papers = {p["cite_key"]: p for p in _rows(enriched.get("papers")) if _present(p.get("cite_key"))}
    cards = read_rows("paper_annotations")
    direct, document = _owners(passages, papers, [card for _, card in cards])

    def owners(ref):
        # A single JSON file may contain many papers. Individual source IDs take priority.
        return direct.get(ref["source_id"]) or document.get(ref["path"], set())

    def verified_refs(row, label):
        refs = row.get("evidence", [])
        if not isinstance(refs, list):
            report("expected_array", label + ".evidence", "Supply source_id/quote objects in an array.")
            refs = []
        verified = []
        for i, ref in enumerate(refs):
            sid = ref.get("source_id") if isinstance(ref, dict) else ""
            quote = ref.get("quote") if isinstance(ref, dict) else ""
            passage = passages.get(sid) if isinstance(sid, str) else None
            if (not passage or not _present(quote) or normalized(quote) not in normalized(passage["text"])
                    or passage.get("role") == "instruction"):
                report("source_quote_mismatch", label, "Copy a contiguous quotation from the cited source passage.",
                       source_id=sid, evidence_index=i)
                continue
            verified.append({"source_id": sid, "quote": quote, "path": passage["path"],
                             "locator": passage["locator"], "role": passage.get("role")})
        if not verified:
            report("missing_source_evidence", label, "Attach a verifiable quotation from the case materials.")
        return verified

    def literature_refs(refs, keys, label):
        accepted = []
        covered = set()
        uncertain = False
        for ref in refs:
            matches = owners(ref)
            if ref["role"] == "study":
                report("paper_evidence_from_study", label, "Attach the cited work or a supplied literature note.", source_id=ref["source_id"])
                continue
            if keys and matches and not matches.intersection(keys):
                report("wrong_paper_source", label, "Replace this quotation with one from the cited work.",
                       source_id=ref["source_id"], expected=sorted(keys), identified=sorted(matches))
                continue
            if len(matches) == 1:
                covered.update(matches)
            else:
                uncertain = True
            accepted.append(ref)
        missing = keys - covered
        if missing and accepted:
            report("review_work_identity" if uncertain else "missing_cited_work_evidence", label,
                   "Link each cited work to its own excerpt, using its title or DOI to reconcile separate files.",
                   review=uncertain, cite_keys=sorted(missing))
        return accepted

    def study_refs(refs, label):
        accepted = []
        for ref in refs:
            if ref["role"] == "literature" or (ref["role"] != "study" and owners(ref)):
                report("contribution_uses_prior_work", label, "Use the author's method, table, figure or results for this claim.",
                       source_id=ref["source_id"])
            else:
                accepted.append(ref)
        if accepted and all(ref["role"] != "study" for ref in accepted):
            report("review_study_ownership", label, "Confirm that these unclassified files describe the author's own work.",
                   review=True, source_ids=[r["source_id"] for r in accepted])
        return accepted

    def check_substance(refs, label):
        if not refs:
            return
        boilerplate = re.compile(r"(?:the|of|a|and|this|that|or|in|的|和|本文|研究|(?:figure|fig\.?|table|图|表)\s*\d+[a-z]?)", re.I)
        if all(META_LOCATOR.search(r["locator"]) or boilerplate.fullmatch(r["quote"].strip()) or
               not re.search(r"[^\W\d_]{2}", r["quote"]) for r in refs):
            report("weak_claim_evidence", label, "Quote the actual method, finding or measurement with its context.")
        elif all(len(_fold(r["quote"])) < 8 for r in refs):
            report("review_short_evidence", label, "Check that these short quotations identify the measured quantity and its context.", review=True)

    def check_quantities(row, refs, label):
        claim = row["claim"]
        evidence_text = " ".join(r["quote"] for r in refs)
        observed = _quantities(evidence_text)
        for unit, numbers in _quantities(claim).items():
            missing = numbers - observed.get(unit, set())
            if missing and unit in observed:
                calculation = _present(row.get("calculation"))
                report("review_derived_quantity" if calculation else "source_quantity_mismatch", label,
                       "Recompute the reported value from its stated inputs and units." if calculation else
                       "Use the value and unit in the quoted author evidence, or document the calculation.",
                       review=calculation, unit=unit, claimed=sorted(missing), source_values=sorted(observed[unit]))

    validated_cards, seen_keys = [], {}
    for i, card in cards:
        key = card.get("cite_key")
        if not _present(key) or not re.fullmatch(r"[\w:.+/-]+", key):
            report("invalid_citation_key", f"paper_annotations[{i}]", "Use a nonempty citation identifier.")
            continue
        label = f"paper:{key}"
        if key in seen_keys:
            if card != seen_keys[key]:
                report("duplicate_paper_annotation", label, "Combine the evidence for one work in one paper card; use separate keys for distinct works.")
            continue
        seen_keys[key] = card
        raw = papers.get(key, {})
        if raw.get("doi") and card.get("doi") and _doi(raw["doi"]) != _doi(card["doi"]):
            report("conflicting_paper_doi", label, "The supplied DOI belongs to a different record. Check the key and DOI.")
            continue
        evidence = literature_refs(verified_refs(card, label), {key}, label)
        if not evidence:
            continue
        paper = dict(raw) if raw else {"cite_key": key, "title": "", "authors": [], "year": None,
                                       "venue": "", "doi": None}
        metadata = {}
        for k in ("title", "venue", "doi"):
            if card.get(k) is not None and not isinstance(card[k], str):
                report("invalid_bibliographic_field", label, "Use text for a bibliographic field.", field=k)
            elif _present(card.get(k)):
                metadata[k] = card[k].strip()
        if "authors" in card:
            authors = card["authors"]
            if not isinstance(authors, list) or not all(_present(a) for a in authors):
                report("invalid_author_list", label, "List each supplied author as a nonempty string.")
            else:
                metadata["authors"] = authors
        if card.get("year") is not None:
            if not re.fullmatch(r"\d{4}", str(card["year"])):
                report("invalid_publication_year", label, "Use a four-digit publication year or omit the unknown year.")
            else:
                metadata["year"] = int(card["year"])
        quoted_identity = " ".join(r["quote"] for r in evidence)
        for field, value in metadata.items():
            if not value:
                continue
            if raw.get(field) and _fold(raw[field]) != _fold(value):
                report("review_bibliographic_difference", label, "Check both versions of this bibliographic field; the original indexed value was retained.",
                       review=True, field=field, supplied=value, indexed=raw[field])
            else:
                paper[field] = value
                values = value if isinstance(value, list) else [value]
                if not raw.get(field) and any(_fold(x) not in _fold(quoted_identity) for x in values):
                    report("review_unquoted_bibliographic_field", label, "Cross-check the supplied bibliographic field in the original source.",
                           review=True, field=field)
        for field in ("route", "problem_setting", "evaluation_system", "key_finding", "limitation"):
            if _present(card.get(field)):
                paper[field] = card[field].strip()
        if any(_present(card.get(k)) for k in ("key_finding", "evaluation_system", "limitation")):
            check_substance(evidence, label)
        paper["evidence"] = evidence
        paper["source_ids"] = list(dict.fromkeys(list(paper.get("source_ids", [])) + [e["source_id"] for e in evidence]))
        papers[key] = paper
        validated_cards.append({**card, **{k: paper[k] for k in metadata if k in paper}, "evidence": evidence})
    notes["paper_annotations"] = validated_cards
    enriched["papers"], enriched["paper_count"] = list(papers.values()), len(papers)

    for field in ("research_gaps", "contributions", "claim_evidence"):
        if field not in notes:
            continue
        validated, seen_ids = [], set()
        for i, row in read_rows(field):
            label = f"{field}[{i}]"
            record = dict(row)
            text_key = "gap_statement" if field == "research_gaps" else "claim"
            if not _present(row.get(text_key)):
                report("missing_claim_text", label, f"Supply the manuscript sentence in {text_key}.")
                continue
            kind = row.get("kind", row.get("role"))
            if field == "claim_evidence" and kind is not None and not _present(kind):
                report("invalid_claim_kind", label, "Use a text label such as comparison or contribution.")
                continue
            judgment = field == "research_gaps" and row.get("author_judgment") is True
            refs = verified_refs(row, label) if row.get("evidence") or not judgment else []
            key_field = "derived_from_cite_keys" if field == "research_gaps" else "cite_keys"
            keys = row.get(key_field, [])
            if not isinstance(keys, list) or any(not _present(k) or k not in papers for k in keys):
                report("invalid_gap_citations" if field == "research_gaps" else "invalid_claim_citations", label,
                       f"List valid indexed citation keys in {key_field}.")
                continue
            if field == "research_gaps" and not judgment and not keys:
                report("missing_gap_premises", label, "Cite the work supporting the gap, or label the gap as a bounded author judgment.")
                continue
            if field == "contributions" or kind == "contribution":
                refs = study_refs(refs, label)
                check_quantities(row, refs, label)
            elif keys or field == "research_gaps" or kind in ("comparison", "related_work"):
                refs = literature_refs(refs, set(keys), label)
            if not refs and not judgment:
                continue
            check_substance(refs, label)
            record["evidence"] = refs
            if field in ("research_gaps", "contributions"):
                id_key, prefix = ("gap_id", "G") if field == "research_gaps" else ("contrib_id", "C")
                rid = row.get(id_key, f"{prefix}{i+1}")
                if not _present(rid) or rid in seen_ids:
                    report("invalid_or_duplicate_id", label, f"Supply a unique nonempty {id_key}.")
                    continue
                seen_ids.add(rid)
                record[id_key] = rid
                record["grounded"] = bool(refs)
            if field == "research_gaps":
                record[key_field] = keys
            if field == "contributions":
                if row.get("addresses_gap_id") is not None and not _present(row["addresses_gap_id"]):
                    report("invalid_target_gap", label, "Use one gap_id string.")
                    continue
                if "evidence_anchor" in row and not isinstance(row["evidence_anchor"], dict):
                    report("invalid_evidence_anchor", label, "Supply a figure_or_table_ref, method_ref or metric_value in an object.")
                    continue
            validated.append(record)
        notes[field] = validated
    enriched["claim_evidence"] = notes.get("claim_evidence", [])
    if isinstance(notes.get("section_spans"), dict):
        enriched["section_spans"] = notes["section_spans"]
    return enriched, notes, issues
