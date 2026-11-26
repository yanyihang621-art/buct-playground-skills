"""Exercise publication, row attribution and derived differences using an editable example."""
import json
from pathlib import Path

from build_methods_result import run_methods_pipeline
from exp_utils import write_json_atomic, write_text_lf
from extract_experiment_facts import extract_experiment_facts
from source_labels import label_for


def comparison_regression(root):
    examples = Path(__file__).resolve().parents[1] / "examples"
    inputs, work = root / "input", root / "work"
    case = json.loads((examples / "example_case.json").read_text(encoding="utf-8"))
    case["output_contract"]["properties"]["run_comparisons"] = {"type": "array"}
    write_json_atomic(inputs / "case.json", case)
    write_text_lf(inputs / "records" / "sensors.csv",
                  "method,dataset,split,metric,value,unit,budget\n"
                  "Smooth,BenchTrace,test,absolute error,8.40,ms,one pass\n"
                  "Raw,BenchTrace,test,absolute error,10.00,ms,one pass\n"
                  "Long,BenchTrace,test,absolute error,6.0,ms,two passes\n"
                  "Zero,BenchTrace,test,absolute error,0,ms,one pass\n")
    write_text_lf(inputs / "records" / "validation.jsonl", json.dumps(
        {"model": "Smooth", "dataset": "BenchTrace", "split": "validation", "metrics": {"mae": 4.1}}))
    write_json_atomic(inputs / "records" / "other.json", {"runs": [
        {"model": "Raw", "dataset": "BenchTrace", "split": "validation", "metrics": {"mae": 4.9}}]})
    write_text_lf(inputs / "records" / "reproduction.md", "**Code version:** track-release\nData access: bench archive\n")
    facts = extract_experiment_facts(inputs, case)["facts"]
    rows = {r["arm"]: r for r in facts["run_observations"] if r["metric"] == "absolute error"}
    base = (examples / "tutorial_annotated.md").read_text(encoding="utf-8")
    draft, review = root / "authored.md", root / "review.json"

    def publish(name, added, pairs):
        write_text_lf(draft, base + "\n\n" + added + "\n")
        write_json_atomic(review, {"comparisons": pairs})
        return run_methods_pipeline(inputs, work, root / name, draft_file=draft, review_file=review,
                                    manuscript_file="paper/revised.md", contract_values=examples / "tutorial_contract.json")

    def pair(left, reference, op="difference"):
        return {"left": rows[left]["id"], "reference": rows[reference]["id"], "operation": op}

    good = publish("good", "{{comparison gap}}\n{{comparison percent}}",
                   {"gap": pair("Smooth", "Raw"), "percent": pair("Smooth", "Raw", "relative_change")})
    broken = publish("bad", "{{comparison unequal}}\n{{comparison zero}}",
                     {"unequal": pair("Smooth", "Long"), "zero": pair("Smooth", "Zero", "relative_change")})
    labels = [label_for(row) for row in facts["evidence"] if row["source"] == "records/sensors.csv"
              and row["locator"] in ("row2", "row3")]
    mark = "⟦" + ",".join(labels) + "⟧"
    swapped = publish("swapped", "Smooth scored 10.00 ms and Raw scored 8.40 ms." + mark, {})
    valid = publish("unchanged", "Smooth scored 8.40 ms and Raw scored 10.00 ms." + mark, {})
    comparison = good["result"]["run_comparisons"]
    return {
        "source_formats": {r["source"] for r in facts["run_observations"]} ==
                          {"records/sensors.csv", "records/validation.jsonl", "records/other.json"},
        "file_and_locator": good["result"]["status"] == "completed" and
                            (root / "good/paper/revised.md").read_text(encoding="utf-8").strip() == good["chapter_md"].strip() and
                            all(row["left"]["locator"] == "row2" and row["reference"]["locator"] == "row3" for row in comparison),
        "signed_difference_and_percent": [row["value"] for row in comparison] == ["-1.60", "-16.00"] and
                                         "-1.60 ms" in good["chapter_md"] and "-16.00 %" in good["chapter_md"],
        "unsupported_comparisons_flagged": broken["result"]["status"] == "partial" and
                                           {i.get("name") for i in broken["result"]["uncertainties"]
                                            if i.get("code") == "unverified_comparison"} == {"unequal", "zero"},
        "correct_attribution_accepted": valid["result"]["status"] == "completed",
        "swapped_attribution_rejected": any(i.get("code") == "source_arm_value_mismatch" for i in swapped["result"]["uncertainties"]),
        "checklist_cites_actual_notebook": "track-release (records/reproduction.md L1)" in good["repro_md"] and
                                          "bench archive (records/reproduction.md L2)" in good["repro_md"],
    }
