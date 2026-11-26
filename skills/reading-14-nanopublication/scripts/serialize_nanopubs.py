#!/usr/bin/env python3
"""Zero-dependency W3C JSON-LD 1.1, TriG, N-Quads, and Markdown serializers + N-Quads syntax verifier."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from pdf_utils import write_json_atomic, write_text_lf
from decompose_assertions import location_label

PREFIXES = {
    "np": "http://www.nanopub.org/nschema#",
    "prov": "http://www.w3.org/ns/prov#",
    "pav": "http://purl.org/pav/",
    "dct": "http://purl.org/dc/terms/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "npv": "https://w3id.org/np/vocab/",
}

NQUAD_LINE_RE = re.compile(
    r"^<([^\s<>\"{}|^`\\]+)>\s+<([^\s<>\"{}|^`\\]+)>\s+(<([^\s<>\"{}|^`\\]+)>|\"(?:[^\"\\]|\\.)*\"(?:@[A-Za-z-]+|\^\^<[^\s<>\"{}|^`\\]+>)?)\s+<([^\s<>\"{}|^`\\]+)>\s+\.$"
)


def _slug_iri(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_]+", "_", text.strip()).strip("_")[:48]
    if not slug:
        slug = "entity"
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return f"https://w3id.org/np/entity/{slug}_{h}"


def _esc_rdf_literal(text: str) -> str:
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def _iri(val: str) -> str:
    return f"<{val}>"


def _lit(val: str, dtype: Optional[str] = None) -> str:
    esc = _esc_rdf_literal(val)
    if dtype:
        return f'"{esc}"^^<{dtype}>'
    return f'"{esc}"'


def serialize_to_jsonld(bundle: Dict[str, Any]) -> Dict[str, Any]:
    """Serialize Nanopublication collection to W3C JSON-LD 1.1 with named graphs."""
    context = dict(PREFIXES)
    graphs: List[Dict[str, Any]] = []

    for np_obj in bundle.get("nanopublications", []):
        uri = np_obj["nanopub_uri"]
        head = np_obj["head"]
        a = np_obj["assertion"]
        p = np_obj["provenance"]
        pi = np_obj["pubinfo"]
        loc = p.get("location", {})
        smeta = p.get("source_metadata", {})

        subj_iri = _slug_iri(a["subject"])
        obj_iri = _slug_iri(a["object"])

        # Head graph
        graphs.append(
            {
                "@id": f"{uri}#Head",
                "@graph": [
                    {
                        "@id": uri,
                        "@type": "np:Nanopublication",
                        "np:hasAssertion": {"@id": head["assertion_graph"]},
                        "np:hasProvenance": {"@id": head["provenance_graph"]},
                        "np:hasPublicationInfo": {"@id": head["pubinfo_graph"]},
                    }
                ],
            }
        )
        # Reifying a qualified statement avoids asserting a conditional, negated
        # or hypothetical relation as an unconditional RDF edge.
        modality = a.get("modality", "asserted")
        polarity = a.get("polarity", "affirmative")
        statement_node = {
            "@id": f"{uri}#statement", "@type": "rdf:Statement",
            "rdf:subject": {"@id": subj_iri},
            "rdf:predicate": {"@id": a["predicate_uri"]},
            "rdf:object": {"@id": obj_iri},
            "npv:atomicStatement": a["atomic_statement"],
            "npv:modality": modality, "npv:polarity": polarity,
            "npv:conditions": a.get("conditions", []),
        }
        subject_node = {"@id": subj_iri, "rdfs:label": a["subject"]}
        if (modality == "asserted" and polarity == "affirmative" and not a.get("conditions")
                and p.get("evidence_type") not in {"indirect_inference", "cited_prior_work"}):
            subject_node[a["predicate_uri"]] = {"@id": obj_iri}
        graphs.append({"@id": head["assertion_graph"], "@graph": [
            statement_node, subject_node, {"@id": obj_iri, "rdfs:label": a["object"]}
        ]})
        # Preserve both the paper identity and the native address in each export.
        source_iri = ("https://doi.org/" + smeta["doi"] if smeta.get("doi") not in (None, "", "not_reported")
                      else "urn:sha256:" + str(smeta.get("source_sha256", "unknown")))
        graphs.append(
            {
                "@id": head["provenance_graph"],
                "@graph": [
                    {
                        "@id": head["assertion_graph"],
                        "prov:wasDerivedFrom": {"@id": source_iri},
                        "npv:evidenceType": p.get("evidence_type"),
                        "npv:evidenceSnippet": p.get("minimal_evidence_snippet"),
                        "npv:conditionEvidence": p.get("condition_quote") or None,
                        "npv:sourceHash": smeta.get("source_sha256"),
                        "npv:pdfPage": loc.get("pdf_page"),
                        "npv:locationId": loc.get("block_id") or loc.get("table_id") or "unknown",
                        "npv:bbox": json.dumps(loc["bbox"]) if "bbox" in loc else None,
                        "npv:lineStart": loc.get("line_start"),
                        "npv:lineEnd": loc.get("line_end"),
                        "npv:nativeParagraphId": loc.get("original_id"),
                        "npv:sourceSection": loc.get("section") or loc.get("section_id"),
                        "npv:sourceAddress": loc.get("json_pointer") or loc.get("xml_xpath"),
                        "npv:tableRow": loc.get("row_index"),
                        "npv:tableColumn": loc.get("column_name"),
                        "dct:title": smeta.get("paper_title"),
                    },
                    {"@id": source_iri, "dct:title": smeta.get("paper_title"), "dct:creator": smeta.get("authors", []),
                     "dct:issued": smeta.get("year"), "pav:version": smeta.get("version"),
                     "npv:sourceFile": smeta.get("source_file"), "npv:sourceHash": smeta.get("source_sha256")}
                ],
            }
        )
        # PubInfo graph
        graphs.append(
            {
                "@id": head["pubinfo_graph"],
                "@graph": [
                    {
                        "@id": uri,
                        "dct:created": {"@value": pi.get("created_at", ""), "@type": "xsd:dateTime"},
                        "pav:createdBy": pi.get("creator", ""),
                        "npv:confidenceScore": {"@value": str(pi.get("confidence_score", 0.0)), "@type": "xsd:decimal"},
                        "npv:confidenceBasis": pi.get("confidence_basis", ""),
                    }
                ],
            }
        )

    return {"@context": context, "@graph": graphs}


def serialize_to_trig_and_nquads(bundle: Dict[str, Any]) -> Tuple[str, str]:
    """Serialize Nanopublication collection to W3C TriG and W3C N-Quads."""
    def expand(iri: str) -> str:
        prefix, _, local = iri.partition(":")
        return PREFIXES[prefix] + local if prefix in PREFIXES else iri

    def object_token(value: Any) -> str:
        if isinstance(value, dict):
            if "@id" in value:
                return _iri(expand(value["@id"]))
            return _lit(value["@value"], expand(value["@type"]))
        if isinstance(value, int):
            return _lit(str(value), PREFIXES["xsd"] + "integer")
        if isinstance(value, float):
            return _lit(str(value), PREFIXES["xsd"] + "double")
        return _lit(value)

    trig_lines: List[str] = []
    nq_lines: List[str] = []
    for graph in serialize_to_jsonld(bundle)["@graph"]:
        graph_iri = _iri(graph["@id"])
        trig_lines.append(f"{graph_iri} {{")
        for node in graph["@graph"]:
            subject = _iri(node["@id"])
            for key, values in node.items():
                if key == "@id" or values is None:
                    continue
                predicate = _iri(expand("rdf:type" if key == "@type" else key))
                for value in values if isinstance(values, list) else [values]:
                    obj = _iri(expand(value)) if key == "@type" else object_token(value)
                    triple = f"{subject} {predicate} {obj}"
                    trig_lines.append(f"  {triple} .")
                    nq_lines.append(f"{triple} {graph_iri} .")
        trig_lines.append("}")
    return "\n".join(trig_lines) + "\n", ("\n".join(nq_lines) + "\n" if nq_lines else "")


def verify_nquads_syntax(nq_text: str) -> Dict[str, Any]:
    """Deterministically verify W3C N-Quads line grammar and 4-graph completeness."""
    errors: List[str] = []
    quad_count = 0
    graphs_seen = {"Head": 0, "assertion": 0, "provenance": 0, "pubinfo": 0}

    for ln_no, raw in enumerate(nq_text.splitlines(), start=1):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        m = NQUAD_LINE_RE.match(s)
        if not m:
            errors.append(f"Line {ln_no}: invalid N-Quads grammar: {s[:100]}")
            continue
        quad_count += 1
        g_iri = m.group(5)
        for suffix in graphs_seen:
            if g_iri.endswith(f"#{suffix}"):
                graphs_seen[suffix] += 1

    return {
        "valid": len(errors) == 0,
        "quad_count": quad_count,
        "graph_quad_counts": graphs_seen,
        "errors": errors,
    }


def serialize_to_markdown(bundle: Dict[str, Any]) -> str:
    """Render human-reviewable Markdown summary table of Nanopublications."""
    smeta = bundle.get("source_metadata", {})
    lines = [
        f"# Nanopublications: {smeta.get('paper_title', 'Document')}",
        "",
        f"- **Status:** `{bundle.get('status')}` | **Count:** {bundle.get('nanopub_count', 0)}",
        f"- **Authors:** {', '.join(smeta.get('authors', []))} | **Year:** {smeta.get('year')} | **DOI:** {smeta.get('doi')}",
        "",
        "| ID | Atomic statement | Predicate | Conditions | Evidence type | Source and location | Evidence | Confidence |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for np_obj in bundle.get("nanopublications", []):
        a = np_obj["assertion"]
        p = np_obj["provenance"]
        loc = p.get("location", {})
        pi = np_obj["pubinfo"]
        eid = loc.get("block_id") or loc.get("table_id") or "-"
        position = location_label(loc)
        prov_str = f"{p.get('source_metadata', {}).get('source_file')} / {position} / {eid}"
        conds = ", ".join(a.get("conditions", [])) or "—"

        def _c(x: Any) -> str:
            return str(x).replace("|", "\\|").replace("\n", " ")

        lines.append(
            f"| `{np_obj['nanopub_id']}` | {_c(a['atomic_statement'])} | `{a['predicate']}` | "
            f"{_c(conds)} | `{p.get('evidence_type')}` | {_c(prov_str)} | {_c(p.get('minimal_evidence_snippet', ''))} | {pi.get('confidence_score')} |"
        )
    if bundle.get("conflicts"):
        lines.extend(["", "## Detected Cross-Assertion Conflicts"])
        for cf in bundle["conflicts"]:
            lines.append(f"- **`{cf['conflict_id']}`:** {cf['message']}")
    return "\n".join(lines) + "\n"


def serialize_all(bundle: Dict[str, Any], out_dir: Path) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonld_obj = serialize_to_jsonld(bundle)
    trig_str, nq_str = serialize_to_trig_and_nquads(bundle)
    md_str = serialize_to_markdown(bundle)
    nq_check = verify_nquads_syntax(nq_str)

    write_json_atomic(out_dir / "nanopubs.jsonld", jsonld_obj)
    write_text_lf(out_dir / "nanopubs.trig", trig_str)
    write_text_lf(out_dir / "nanopubs.nq", nq_str)
    write_text_lf(out_dir / "nanopubs.md", md_str)
    return {
        "jsonld_path": str(out_dir / "nanopubs.jsonld"),
        "trig_path": str(out_dir / "nanopubs.trig"),
        "nquads_path": str(out_dir / "nanopubs.nq"),
        "markdown_path": str(out_dir / "nanopubs.md"),
        "nquads_syntax": nq_check,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Serialize nanopubs.json into JSON-LD, TriG, N-Quads, and Markdown.")
    parser.add_argument("--nanopubs", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    bundle = json.loads(Path(args.nanopubs).read_text(encoding="utf-8"))
    res = serialize_all(bundle, Path(args.out))
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
