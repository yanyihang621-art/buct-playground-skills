"""Run only the labeled tutorial, optionally using real PDF and DOCX inputs."""
import argparse
import json
from pathlib import Path
import shutil
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
from build_intro_rw_result import run_intro_rw_pipeline
from ingest_materials import ingest_materials
from rw_utils import write_json_atomic, write_text_lf

# All study names and observations here are synthetic tutorial data.
A = "Calibration was evaluated on stationary sensors. The devices remained indoors."
B = "Mobile sensors were evaluated indoors. The protocol excluded outdoor routes."
STUDY = "Field protocol, Table 1: We recorded mobile sensors on an outdoor route."
BACKGROUND = "Placement and exposure define which sources of sensor drift a calibration study can observe."
COMPARE = "Both evaluations used indoor settings, but only the second varied sensor placement"
GAP = "How does calibration drift change when a sensor moves outdoors?"
CONTRIB = "We therefore record mobile sensors along an outdoor route, extending the sampled setting to outdoor movement."
LIMIT = "The field protocol establishes the sampling design. An estimate of drift under that design requires the measurements."
DRAFT = f"""## Research Background

{BACKGROUND} These design choices delimit what can be inferred from the two available evaluations [@fixed; @mobile].

## Related Work

{COMPARE} [@fixed; @mobile]. Holding placement fixed isolates observations from movement, whereas an indoor mobile protocol can expose movement-related variation without testing outdoor conditions.

## Research Gaps

{GAP} The stationary and indoor mobile studies leave this joint condition outside their respective observation windows [@fixed; @mobile].

## Contributions

{CONTRIB} {LIMIT}

## References

- Reed, P. (2021). Fixed placement. Measurement Notes. [@fixed]
- Lin, J. (2023). Indoor mobility. Instrument Reports. [@mobile]
"""


def run(root, mixed=False):
    inp = root / "input"
    papers = inp / "materials" / "papers"
    papers.mkdir(parents=True, exist_ok=True)
    study = inp / "author"
    study.mkdir(exist_ok=True)
    shutil.copyfile(HERE / "example_case.json", inp / "case.json")
    write_text_lf(papers / "fixed.md", "Reed, P. (2021). Fixed placement. Measurement Notes.\n\n" + A)
    lit_text = "Lin, J. (2023). Indoor mobility. Instrument Reports.\n\n" + B
    if mixed:
        import fitz
        from docx import Document
        with fitz.open() as pdf:
            page = pdf.new_page()
            page.insert_textbox((56, 56, 510, 310), lit_text)
            pdf.save(papers / "mobile.pdf")
        doc = Document()
        doc.add_paragraph("Field protocol")
        doc.add_table(rows=1, cols=1).cell(0, 0).text = STUDY
        doc.save(study / "protocol.docx")
    else:
        write_text_lf(papers / "mobile.txt", lit_text)
        write_text_lf(study / "protocol.md", STUDY)
    case = json.loads((inp / "case.json").read_text(encoding="utf-8"))
    index = ingest_materials(inp, case)
    def evidence(phrase):
        for passage in index["passages"]:
            if phrase in " ".join(passage["text"].split()):
                return {"source_id": passage["source_id"], "quote": phrase}
        raise ValueError(f"Tutorial source passage missing: {phrase}")
    a, b, own = [evidence(phrase) for phrase in (A, B, STUDY)]
    question = {"gap_id":"G1", "gap_statement":GAP, "derived_from_cite_keys":["fixed","mobile"], "evidence":[a,b]}
    contribution = {"contrib_id":"C1","claim":CONTRIB,"addresses_gap_id":"G1",
                    "evidence_anchor":{"figure_or_table_ref":"Table 1"},"evidence":[own]}
    cards = [{"kind":kind,"claim":claim,"evidence":refs} for kind, claim, refs in
             [("background",BACKGROUND,[a,b]),("comparison",COMPARE,[a,b]),("gap",GAP,[a,b]),("contribution",CONTRIB,[own])]]
    analysis = {"paper_annotations":[
        {"cite_key":"fixed","title":"Fixed placement","authors":["Reed, P."],"year":2021,"venue":"Measurement Notes",
         "route":"Fixed placement","problem_setting":"Indoor drift","evaluation_system":"Stationary sensors",
         "limitation":"Position was fixed","evidence":[evidence("Reed, P. (2021). Fixed placement. Measurement Notes."),a]},
        {"cite_key":"mobile","title":"Indoor mobility","authors":["Lin, J."],"year":2023,"venue":"Instrument Reports",
         "route":"Mobile placement","problem_setting":"Indoor drift","evaluation_system":"Mobile sensors",
         "limitation":"Outdoor movement was excluded","evidence":[evidence("Lin, J. (2023). Indoor mobility. Instrument Reports."),b]}],
        "research_gaps":[question],"contributions":[contribution],"claim_evidence":cards,
        "contract_fields":{"bounded_question":GAP,"study_evidence":["Field protocol, Table 1"]},
        "interpretation_limits":[{"kind":"design_only","manuscript_span":LIMIT,
                                  "reason":"The protocol supplies an acquisition design.", "evidence":[own]}],
        "change_records":[{"action":"scoped_gap","reason":"Both evaluations used indoor protocols.",
                           "source_ids":[a["source_id"],b["source_id"]]}]}
    draft_path, notes_path = root / "authored.md", root / "notes.json"
    write_text_lf(draft_path, DRAFT)
    write_json_atomic(notes_path, analysis)
    return run_intro_rw_pipeline(inp, root / "work", root / "output", draft_file=draft_path, analysis_file=notes_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--mixed-documents", action="store_true")
    args = parser.parse_args()
    result = run(args.out_dir, args.mixed_documents)["result"]
    print(json.dumps({"status":result["status"],"verification":result["verification"],
                      "source_count":len(result["sources"]), "output":str(args.out_dir / "output")}))
    raise SystemExit(0 if result["status"] == "completed" else 1)
