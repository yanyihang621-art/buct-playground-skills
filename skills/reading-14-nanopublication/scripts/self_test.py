"""End-to-end self-test, RDF roundtrip check, and fault-injection benchmark for reading-14-nanopublication."""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import fitz

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from finalize import run_finalize
from output_contract import contract_issues
from pdf_utils import write_json_atomic, write_text_lf
from prepare import run_prepare


def _make_encrypted_pdf(dest: Path) -> Path:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Confidential fixture for encryption fallback test.")
    doc.save(
        str(dest),
        encryption=fitz.PDF_ENCRYPT_AES_256,
        user_pw="secret123",
        owner_pw="owner123",
    )
    doc.close()
    return dest


def _find_encrypted_fixture(tmp_dir: Path) -> Path:
    for candidate in (
        SKILL_DIR.parents[2] / "local_test_outputs" / "reading-01" / "fixtures" / "t3a_encrypted.pdf",
    ):
        if candidate.is_file():
            return candidate
    return _make_encrypted_pdf(tmp_dir / "encrypted_fixture.pdf")


def run_suite(out_dir: Path, write_benchmark: bool = False) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    examples_dir = SKILL_DIR / "examples"
    case_path = examples_dir / "example_case.json"
    case_data = json.loads(case_path.read_text(encoding="utf-8-sig"))
    contract = case_data["output_contract"]

    results: Dict[str, Any] = {"skill": "reading-14-nanopublication", "tests": {}, "all_passed": False}

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        # (a) & (b) & (d): Happy path + closed contract projection + RDF consistency
        in1 = tmp_path / "happy_in"
        w1 = tmp_path / "happy_work"
        o1 = tmp_path / "happy_out"
        in1.mkdir(parents=True, exist_ok=True)
        shutil.copy2(examples_dir / "example_paper.md", in1 / "example_paper.md")
        shutil.copy2(case_path, in1 / "case.json")
        run_prepare(in1, in1 / "case.json", w1, o1)
        shutil.copy2(examples_dir / "example_claims.json", w1 / "claims.json")
        shutil.copy2(examples_dir / "example_answer.json", w1 / "answer.json")
        rc = run_finalize(in1 / "case.json", w1, o1)

        res1 = json.loads((o1 / "result.json").read_text(encoding="utf-8"))
        ev1 = json.loads((o1 / "evidence_check.json").read_text(encoding="utf-8"))
        issues1 = contract_issues(contract, res1)
        extra_keys = sorted(set(res1.keys()) - set(contract["properties"].keys()))
        rdf_info = ev1.get("rdf_validation") or {}
        rdf_ok = bool(rdf_info.get("consistent")) and (o1 / "nanopubs.trig").is_file() and (o1 / "nanopubs.nq").is_file() and (o1 / "nanopubs.jsonld").is_file()
        happy_ok = (
            rc == 0
            and res1.get("case_id") == case_data["case_id"]
            and len(res1.get("nanopublications") or []) == 4
            and not issues1
            and not extra_keys
            and ev1["counts"]["unverified"] == 0
            and rdf_ok
        )
        results["tests"]["happy_closed_and_rdf"] = {
            "passed": happy_ok,
            "contract_issues": issues1,
            "extra_keys": extra_keys,
            "counts": ev1["counts"],
            "rdf_validation": rdf_info,
        }

        # (c) Fault injection checks
        fault_cases = [
            ("altered_quote", "unverified_quote"),
            ("dropped_negation", "negation_dropped"),
            ("altered_number", "number_not_in_evidence"),
            ("wrong_anchor", "anchor_relocated"),
            ("may_as_asserted", "hedge_mismatch"),
        ]
        fault_summary: Dict[str, Any] = {}
        base_claims = json.loads((examples_dir / "example_claims.json").read_text(encoding="utf-8-sig"))
        base_answer = json.loads((examples_dir / "example_answer.json").read_text(encoding="utf-8-sig"))

        for name, expected_code in fault_cases:
            wf = tmp_path / f"work_{name}"
            of = tmp_path / f"out_{name}"
            run_prepare(in1, in1 / "case.json", wf, of)
            c_mod = copy.deepcopy(base_claims)
            a_mod = copy.deepcopy(base_answer)

            if name == "altered_quote":
                c_mod["claims"][0]["evidence"][0]["quote"] = "Oxygen vacancies erase all methanol yield at 573 K."
            elif name == "dropped_negation":
                c_mod["claims"][2]["polarity"] = "affirmative"
                c_mod["claims"][2]["statement"] = "Carrier gas switching changed CO2 conversion across duplicate runs."
            elif name == "altered_number":
                c_mod["claims"][3]["statement"] = "Cu-In2O3 achieves 98.7% methanol selectivity at 573 K."
                c_mod["claims"][3]["qualifiers"]["value"] = "98.7"
            elif name == "wrong_anchor":
                c_mod["claims"][3]["evidence"][0]["anchor"] = "p1_b03"
            elif name == "may_as_asserted":
                c_mod["claims"][1]["modality"] = "asserted"

            write_json_atomic(wf / "claims.json", c_mod)
            write_json_atomic(wf / "answer.json", a_mod)
            run_finalize(in1 / "case.json", wf, of)
            evf = json.loads((of / "evidence_check.json").read_text(encoding="utf-8"))
            all_codes = (
                [x.get("code") for x in evf.get("must_fix", [])]
                + [x.get("code") for x in evf.get("warnings", [])]
                + [x.get("type") for x in evf.get("change_records", [])]
            )
            fault_summary[name] = {
                "expected": expected_code,
                "detected": expected_code in all_codes,
                "observed_codes": sorted(set(c for c in all_codes if c)),
            }

        results["tests"]["fault_injection"] = {
            "passed": all(v["detected"] for v in fault_summary.values()),
            "cases": fault_summary,
        }

        # (e) Encrypted PDF honest degradation
        enc_pdf = _find_encrypted_fixture(tmp_path)
        in_enc = tmp_path / "enc_in"
        w_enc = tmp_path / "enc_work"
        o_enc = tmp_path / "enc_out"
        in_enc.mkdir(parents=True, exist_ok=True)
        shutil.copy2(enc_pdf, in_enc / "locked.pdf")
        enc_case = {
            "case_id": "enc-test-14",
            "input_files": ["locked.pdf"],
            "output_contract": {
                "type": "object",
                "additionalProperties": False,
                "required": ["case_id", "status", "uncertainties"],
                "properties": {
                    "case_id": {"type": "string"},
                    "status": {"type": "string", "enum": ["success", "partial", "unable"]},
                    "uncertainties": {"type": "array"},
                },
            },
        }
        write_json_atomic(in_enc / "case.json", enc_case)
        run_prepare(in_enc, in_enc / "case.json", w_enc, o_enc)
        res_enc = json.loads((o_enc / "result.json").read_text(encoding="utf-8"))
        enc_ok = (
            res_enc.get("case_id") == "enc-test-14"
            and res_enc.get("status") in {"unable", "partial"}
            and not contract_issues(enc_case["output_contract"], res_enc)
        )
        results["tests"]["encrypted_pdf_fallback"] = {
            "passed": enc_ok,
            "status": res_enc.get("status"),
            "fixture": str(enc_pdf),
        }

    results["all_passed"] = all(t["passed"] for t in results["tests"].values())
    write_json_atomic(out_dir / "self_test_report.json", results)

    if write_benchmark:
        bench_dir = SKILL_DIR / "benchmarks"
        bench_dir.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Reading 14 校验器故障注入与 RDF 一致性实测记录",
            "",
            f"- 运行时间 (UTC)：`{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}`",
            f"- 测试脚本：`scripts/self_test.py`",
            f"- 全部测试通过：`{results['all_passed']}`",
            f"- RDF 导出校验：`{json.dumps(results['tests']['happy_closed_and_rdf']['rdf_validation'], ensure_ascii=False)}`",
            "",
            "| 测试项 / 注入故障 | 期望检出码 | 实测检出 | 实际捕获编码 |",
            "|---|---|---|---|",
        ]
        for cname, cval in results["tests"]["fault_injection"]["cases"].items():
            lines.append(
                f"| {cname} | `{cval['expected']}` | {'PASS' if cval['detected'] else 'FAIL'} | "
                f"`{', '.join(cval['observed_codes'])}` |"
            )
        lines.extend([
            f"| happy_closed_and_rdf | 契约零错 + RDF 跨格式一致 | {'PASS' if results['tests']['happy_closed_and_rdf']['passed'] else 'FAIL'} | `exact={results['tests']['happy_closed_and_rdf']['counts']['exact']}` |",
            f"| encrypted_pdf_fallback | 状态降级为 unable/partial | {'PASS' if results['tests']['encrypted_pdf_fallback']['passed'] else 'FAIL'} | `status={results['tests']['encrypted_pdf_fallback']['status']}` |",
        ])
        write_text_lf(bench_dir / "validator_benchmark.md", "\n".join(lines) + "\n")

    print(json.dumps(results, ensure_ascii=False, indent=2))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Run reading-14 self-test suite.")
    parser.add_argument("--out-dir", default="/tmp/reading14-selftest", help="Directory to write test report")
    parser.add_argument("--write-benchmark", action="store_true", help="Update benchmarks/validator_benchmark.md")
    args = parser.parse_args()
    res = run_suite(Path(args.out_dir), write_benchmark=args.write_benchmark)
    return 0 if res["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
