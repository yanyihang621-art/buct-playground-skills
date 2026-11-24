#!/usr/bin/env python3
"""Zero-dependency W3C JSON-LD 1.1, TriG, N-Quads, and Markdown serializers + N-Quads syntax verifier."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from pdf_utils import write_json_atomic, write_text_lf

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
        # Assertion graph
        graphs.append(
            {
                "@id": head["assertion_graph"],
                "@graph": [
                    {
                        "@id": subj_iri,
                        "rdfs:label": a["subject"],
                        a["predicate_uri"]: {"@id": obj_iri},
                        "npv:atomicStatement": a["atomic_statement"],
                        "npv:conditions": a.get("conditions", []),
                    },
                    {"@id": obj_iri, "rdfs:label": a["object"]},
                ],
            }
        )
        # Provenance graph
        graphs.append(
            {
                "@id": head["provenance_graph"],
                "@graph": [
                    {
                        "@id": head["assertion_graph"],
                        "prov:wasDerivedFrom": smeta.get("doi") if smeta.get("doi") != "not_reported" else smeta.get("source_file", "unknown"),
                        "npv:evidenceType": p.get("evidence_type"),
                        "npv:evidenceSnippet": p.get("minimal_evidence_snippet"),
                        "npv:pdfPage": loc.get("pdf_page"),
                        "npv:locationId": loc.get("block_id") or loc.get("table_id") or "unknown",
                        "npv:bbox": json.dumps(loc.get("bbox", [])),
                        "dct:title": smeta.get("paper_title"),
                    }
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
                        "npv:confidenceScore": pi.get("confidence_score", 0.0),
                        "npv:confidenceBasis": pi.get("confidence_basis", ""),
                    }
                ],
            }
        )

    return {"@context": context, "@graph": graphs}


def serialize_to_trig_and_nquads(bundle: Dict[str, Any]) -> Tuple[str, str]:
    """Serialize Nanopublication collection to W3C TriG and W3C N-Quads."""
    trig_lines = [f"@prefix {k}: <{v}> ." for k, v in PREFIXES.items()] + [""]
    nq_lines: List[str] = []

    rdf_type = f"{PREFIXES['rdf']}type"
    rdfs_label = f"{PREFIXES['rdfs']}label"
    xsd_dt = f"{PREFIXES['xsd']}dateTime"
    xsd_int = f"{PREFIXES['xsd']}integer"
    xsd_dec = f"{PREFIXES['xsd']}decimal"

    for np_obj in bundle.get("nanopublications", []):
        uri = np_obj["nanopub_uri"]
        head_g = f"{uri}#Head"
        ass_g = np_obj["head"]["assertion_graph"]
        prov_g = np_obj["head"]["provenance_graph"]
        pub_g = np_obj["head"]["pubinfo_graph"]

        a = np_obj["assertion"]
        p = np_obj["provenance"]
        pi = np_obj["pubinfo"]
        loc = p.get("location", {})
        smeta = p.get("source_metadata", {})

        subj_iri = _slug_iri(a["subject"])
        obj_iri = _slug_iri(a["object"])

        # TriG output
        trig_lines.append(f"<{head_g}> {{")
        trig_lines.append(f"  <{uri}> a np:Nanopublication ;")
        trig_lines.append(f"    np:hasAssertion <{ass_g}> ;")
        trig_lines.append(f"    np:hasProvenance <{prov_g}> ;")
        trig_lines.append(f"    np:hasPublicationInfo <{pub_g}> .")
        trig_lines.append("}")
        trig_lines.append("")

        trig_lines.append(f"<{ass_g}> {{")
        trig_lines.append(f'  <{subj_iri}> rdfs:label "{_esc_rdf_literal(a["subject"])}" ;')
        trig_lines.append(f"    <{a['predicate_uri']}> <{obj_iri}> ;")
        if a.get("conditions"):
            trig_lines.append(f'    npv:conditions "{_esc_rdf_literal("; ".join(a["conditions"]))}" ;')
        trig_lines.append(f'    npv:atomicStatement "{_esc_rdf_literal(a["atomic_statement"])}" .')
        trig_lines.append(f'  <{obj_iri}> rdfs:label "{_esc_rdf_literal(a["object"])}" .')
        trig_lines.append("}")
        trig_lines.append("")

        src_ref = smeta.get("doi") if smeta.get("doi") != "not_reported" else smeta.get("source_file", "unknown")
        loc_id = loc.get("block_id") or loc.get("table_id") or "unknown"
        trig_lines.append(f"<{prov_g}> {{")
        trig_lines.append(f'  <{ass_g}> prov:wasDerivedFrom "{_esc_rdf_literal(src_ref)}" ;')
        trig_lines.append(f'    npv:evidenceType "{_esc_rdf_literal(p.get("evidence_type", ""))}" ;')
        trig_lines.append(f'    npv:evidenceSnippet "{_esc_rdf_literal(p.get("minimal_evidence_snippet", ""))}" ;')
        trig_lines.append(f'    npv:pdfPage "{loc.get("pdf_page", 1)}"^^xsd:integer ;')
        trig_lines.append(f'    npv:locationId "{_esc_rdf_literal(loc_id)}" ;')
        trig_lines.append(f'    dct:title "{_esc_rdf_literal(smeta.get("paper_title", ""))}" .')
        trig_lines.append("}")
        trig_lines.append("")

        trig_lines.append(f"<{pub_g}> {{")
        trig_lines.append(f'  <{uri}> dct:created "{_esc_rdf_literal(pi.get("created_at", ""))}"^^xsd:dateTime ;')
        trig_lines.append(f'    pav:createdBy "{_esc_rdf_literal(pi.get("creator", ""))}" ;')
        trig_lines.append(f'    npv:confidenceScore "{pi.get("confidence_score", 0.0)}"^^xsd:decimal ;')
        trig_lines.append(f'    npv:confidenceBasis "{_esc_rdf_literal(pi.get("confidence_basis", ""))}" .')
        trig_lines.append("}")
        trig_lines.append("")

        # N-Quads output
        nq_lines.extend(
            [
                f"{_iri(uri)} {_iri(rdf_type)} {_iri(PREFIXES['np'] + 'Nanopublication')} {_iri(head_g)} .",
                f"{_iri(uri)} {_iri(PREFIXES['np'] + 'hasAssertion')} {_iri(ass_g)} {_iri(head_g)} .",
                f"{_iri(uri)} {_iri(PREFIXES['np'] + 'hasProvenance')} {_iri(prov_g)} {_iri(head_g)} .",
                f"{_iri(uri)} {_iri(PREFIXES['np'] + 'hasPublicationInfo')} {_iri(pub_g)} {_iri(head_g)} .",
                f"{_iri(subj_iri)} {_iri(rdfs_label)} {_lit(a['subject'])} {_iri(ass_g)} .",
                f"{_iri(subj_iri)} {_iri(a['predicate_uri'])} {_iri(obj_iri)} {_iri(ass_g)} .",
                f"{_iri(subj_iri)} {_iri(PREFIXES['npv'] + 'atomicStatement')} {_lit(a['atomic_statement'])} {_iri(ass_g)} .",
                f"{_iri(obj_iri)} {_iri(rdfs_label)} {_lit(a['object'])} {_iri(ass_g)} .",
                f"{_iri(ass_g)} {_iri(PREFIXES['prov'] + 'wasDerivedFrom')} {_lit(src_ref)} {_iri(prov_g)} .",
                f"{_iri(ass_g)} {_iri(PREFIXES['npv'] + 'evidenceType')} {_lit(p.get('evidence_type', ''))} {_iri(prov_g)} .",
                f"{_iri(ass_g)} {_iri(PREFIXES['npv'] + 'evidenceSnippet')} {_lit(p.get('minimal_evidence_snippet', ''))} {_iri(prov_g)} .",
                f"{_iri(ass_g)} {_iri(PREFIXES['npv'] + 'pdfPage')} {_lit(str(loc.get('pdf_page', 1)), xsd_int)} {_iri(prov_g)} .",
                f"{_iri(ass_g)} {_iri(PREFIXES['npv'] + 'locationId')} {_lit(loc_id)} {_iri(prov_g)} .",
                f"{_iri(uri)} {_iri(PREFIXES['dct'] + 'created')} {_lit(pi.get('created_at', ''), xsd_dt)} {_iri(pub_g)} .",
                f"{_iri(uri)} {_iri(PREFIXES['pav'] + 'createdBy')} {_lit(pi.get('creator', ''))} {_iri(pub_g)} .",
                f"{_iri(uri)} {_iri(PREFIXES['npv'] + 'confidenceScore')} {_lit(str(pi.get('confidence_score', 0.0)), xsd_dec)} {_iri(pub_g)} .",
            ]
        )

    return "\n".join(trig_lines).strip() + "\n", ("\n".join(nq_lines).strip() + "\n" if nq_lines else "")


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
        "| ID | Subject | Predicate | Object | Conditions | Evidence Type | Provenance | Confidence |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for np_obj in bundle.get("nanopublications", []):
        a = np_obj["assertion"]
        p = np_obj["provenance"]
        loc = p.get("location", {})
        pi = np_obj["pubinfo"]
        eid = loc.get("block_id") or loc.get("table_id") or "-"
        prov_str = f"p.{loc.get('pdf_page')} / {eid}"
        conds = ", ".join(a.get("conditions", [])) or "—"

        def _c(x: Any) -> str:
            return str(x).replace("|", "\\|").replace("\n", " ")[:120]

        lines.append(
            f"| `{np_obj['nanopub_id']}` | {_c(a['subject'])} | `{a['predicate']}` | {_c(a['object'])} | "
            f"{_c(conds)} | `{p.get('evidence_type')}` | {prov_str} | {pi.get('confidence_score')} |"
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
