"""End-to-end self-test, 4-graph RDF roundtrip, conflict/multi-format, and fault-injection suite for reading-14."""

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

        # 1. Happy path + closed flat contract + 3-format RDF consistency + nanopubs.json + multi-evidence MD
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
        np_json = json.loads((o1 / "nanopubs.json").read_text(encoding="utf-8"))
        issues1 = contract_issues(contract, res1)
        extra_keys = sorted(set(res1.keys()) - set(contract["properties"].keys()))
        rdf_info = ev1.get("rdf_validation") or {}
        rdf_ok = bool(rdf_info.get("consistent")) and (o1 / "nanopubs.trig").is_file() and (o1 / "nanopubs.nq").is_file() and (o1 / "nanopubs.jsonld").is_file()
        happy_ok = (
            rc == 0
            and res1.get("case_id") == case_data["case_id"]
            and len(res1.get("nanopublications") or []) == 4
            and np_json.get("count") == 4
            and not issues1
            and not extra_keys
            and ev1["counts"]["unverified"] == 0
            and rdf_ok
            and (w1 / "case_brief.md").is_file()
            and (w1 / "answer_template.json").is_file()
        )
        results["tests"]["happy_closed_and_rdf"] = {
            "passed": happy_ok,
            "contract_issues": issues1,
            "extra_keys": extra_keys,
            "counts": ev1["counts"],
            "rdf_validation": rdf_info,
        }

        # 2. Nested 4-graph closed contract projection
        w2 = tmp_path / "nested_work"
        o2 = tmp_path / "nested_out"
        nested_case = {
            "case_id": "nested-4graph",
            "input_files": ["example_paper.md"],
            "output_contract": {
                "type": "object",
                "additionalProperties": False,
                "required": ["case_id", "status", "nanopublications"],
                "properties": {
                    "case_id": {"type": "string"},
                    "status": {"type": "string", "enum": ["success", "partial", "unable"]},
                    "nanopublications": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["id", "head", "assertion", "provenance", "pubinfo"],
                            "properties": {
                                "id": {"type": "string"},
                                "head": {"type": "object"},
                                "assertion": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": ["statement", "subject", "predicate", "object", "polarity", "modality"],
                                    "properties": {
                                        "statement": {"type": "string"},
                                        "subject": {"type": "string"},
                                        "predicate": {"type": "string"},
                                        "object": {"type": "string"},
                                        "polarity": {"type": "string"},
                                        "modality": {"type": "string"},
                                    },
                                },
                                "provenance": {"type": "object"},
                                "pubinfo": {"type": "object"},
                            },
                        },
                    },
                },
            },
        }
        in2 = tmp_path / "nested_in"
        in2.mkdir(parents=True, exist_ok=True)
        shutil.copy2(examples_dir / "example_paper.md", in2 / "example_paper.md")
        write_json_atomic(in2 / "case.json", nested_case)
        run_prepare(in2, in2 / "case.json", w2, o2)
        shutil.copy2(examples_dir / "example_claims.json", w2 / "claims.json")
        run_finalize(in2 / "case.json", w2, o2)
        res2 = json.loads((o2 / "result.json").read_text(encoding="utf-8"))
        issues2 = contract_issues(nested_case["output_contract"], res2)
        results["tests"]["nested_4graph_contract"] = {
            "passed": not issues2 and len(res2.get("nanopublications") or []) == 4 and "subject" in (res2["nanopublications"][0].get("assertion") or {}),
            "contract_issues": issues2,
        }

        # 3. Open contract no-clobber + list claims.json + unwrapped answer.json
        w3 = tmp_path / "open_work"
        o3 = tmp_path / "open_out"
        in3 = tmp_path / "open_in"
        in3.mkdir(parents=True, exist_ok=True)
        shutil.copy2(examples_dir / "example_paper.md", in3 / "example_paper.md")
        open_case = {
            "case_id": "open-r14-no-clobber",
            "input_files": ["example_paper.md"],
            "output_contract": {
                "type": "object",
                "required": ["case_id", "status", "sources", "uncertainties"],
                "properties": {
                    "case_id": {"type": "string"},
                    "status": {"type": "string"},
                    "sources": {"type": "array", "items": {"type": "string"}},
                    "uncertainties": {"type": "array", "items": {"type": "string"}},
                },
            },
        }
        write_json_atomic(in3 / "case.json", open_case)
        run_prepare(in3, in3 / "case.json", w3, o3)
        base_claims = json.loads((examples_dir / "example_claims.json").read_text(encoding="utf-8-sig"))
        write_json_atomic(w3 / "claims.json", base_claims["claims"])
        write_json_atomic(w3 / "answer.json", {
            "case_id": "open-r14-no-clobber",
            "status": "success",
            "sources": ["example_paper.md"],
            "uncertainties": ["synthetic test"],
        })
        run_finalize(in3 / "case.json", w3, o3)
        res3 = json.loads((o3 / "result.json").read_text(encoding="utf-8"))
        issues3 = contract_issues(open_case["output_contract"], res3)
        results["tests"]["open_contract_no_clobber"] = {
            "passed": not issues3 and res3.get("sources") == ["example_paper.md"] and res3.get("nanopub_count") == 4,
            "contract_issues": issues3,
        }

        # 4. Multi-format JATS (with table) + multi-paper metadata isolation + unsafe IRI / full-URL DOI handling
        w4 = tmp_path / "jats_work"
        o4 = tmp_path / "jats_out"
        in4 = tmp_path / "jats_in"
        in4.mkdir(parents=True, exist_ok=True)
        write_text_lf(in4 / "p1.jats", (
            "<article><front><article-meta><title-group><article-title>Paper Alpha</article-title></title-group>"
            "<article-id pub-id-type='doi'>https://doi.org/10.1000/alpha test</article-id><pub-date><year>2024</year></pub-date>"
            "</article-meta></front><body><sec><title>Results</title><p id='r1'>Alpha catalyst increases yield by 15%.</p>"
            "<table id='t1'><thead><tr><th>Sample</th><th>Yield</th></tr></thead><tbody><tr><td>Alpha</td><td>95%</td></tr></tbody></table>"
            "</sec></body></article>"
        ))
        write_text_lf(in4 / "p2.jats", (
            "<article><front><article-meta><title-group><article-title>Paper Beta</article-title></title-group>"
            "<article-id pub-id-type='doi'>10.1000/beta.2</article-id><pub-date><year>2025</year></pub-date>"
            "</article-meta></front><body><sec><title>Results</title><p id='r2'>Beta membrane requires feed below 350 K.</p></sec></body></article>"
        ))
        write_json_atomic(in4 / "case.json", {"case_id": "jats-multipaper-iri"})
        run_prepare(in4, in4 / "case.json", w4, o4)
        idx4 = json.loads((w4 / "index.json").read_text(encoding="utf-8"))
        tab_hits = [e for e in idx4 if e["kind"] == "table"]
        write_json_atomic(w4 / "claims.json", {
            "paper": {"title": "Paper Alpha Override"},
            "claims": [
                {
                    "id": "claim 1 / alpha",
                    "paper_id": "paper_1",
                    "statement": "Alpha catalyst increases yield by 15%.",
                    "subject": "Alpha catalyst",
                    "predicate": "increases",
                    "object": "yield by 15%",
                    "polarity": "affirmative",
                    "modality": "asserted",
                    "evidence": [{"anchor": "paper_1:/article/body[1]/sec[1]/p[1]", "quote": "Alpha catalyst increases yield by 15%."}],
                    "evidence_type": "direct_experimental",
                },
                {
                    "id": "claim 2",
                    "paper_id": "paper_2",
                    "statement": "Beta membrane requires feed below 350 K.",
                    "subject": "Beta membrane",
                    "predicate": "requires",
                    "object": "feed below 350 K",
                    "polarity": "affirmative",
                    "modality": "asserted",
                    "evidence": [{"anchor": "paper_2:/article/body[1]/sec[1]/p[1]", "quote": "Beta membrane requires feed below 350 K."}],
                    "evidence_type": "author_statement",
                },
            ],
        })
        run_finalize(in4 / "case.json", w4, o4)
        res4 = json.loads((o4 / "result.json").read_text(encoding="utf-8"))
        ev4 = json.loads((o4 / "evidence_check.json").read_text(encoding="utf-8"))
        nps4 = res4.get("nanopublications") or []
        p2_title = nps4[1]["source_metadata"]["title"] if len(nps4) == 2 else ""
        results["tests"]["multi_format_jats_multipaper_iri"] = {
            "passed": (
                len(tab_hits) == 1
                and len(nps4) == 2
                and p2_title == "Paper Beta"
                and bool((ev4.get("rdf_validation") or {}).get("consistent"))
                and ev4["counts"]["unverified"] == 0
            ),
            "jats_tables": len(tab_hits),
            "p2_title": p2_title,
            "rdf_validation": ev4.get("rdf_validation"),
        }

        # 5. Deduplication + Polarity & Antonym Conflicts
        w5 = tmp_path / "cf_work"
        o5 = tmp_path / "cf_out"
        run_prepare(in1, in1 / "case.json", w5, o5)
        cf_claims = copy.deepcopy(base_claims)
        dup = copy.deepcopy(cf_claims["claims"][0])
        dup["id"] = "c1_dup"
        neg = copy.deepcopy(cf_claims["claims"][0])
        neg["id"] = "c1_neg"
        neg["polarity"] = "negative"
        ant = copy.deepcopy(cf_claims["claims"][0])
        ant["id"] = "c1_ant"
        ant["predicate"] = "decreases"
        cf_claims["claims"].extend([dup, neg, ant])
        write_json_atomic(w5 / "claims.json", cf_claims)
        write_json_atomic(w5 / "answer.json", json.loads((examples_dir / "example_answer.json").read_text(encoding="utf-8-sig")))
        run_finalize(in1 / "case.json", w5, o5)
        ev5 = json.loads((o5 / "evidence_check.json").read_text(encoding="utf-8"))
        cf_types = {c["type"] for c in ev5.get("conflicts", [])}
        results["tests"]["conflict_and_dedup"] = {
            "passed": len(ev5.get("deduplication_log", [])) >= 1 and {"polarity_conflict", "antonym_predicate_conflict"}.issubset(cf_types),
            "dedup_count": len(ev5.get("deduplication_log", [])),
            "conflict_types": sorted(cf_types),
        }

        # 6. Fault injection checks (8 distinct adversarial faults)
        fault_cases = [
            ("altered_quote", "unverified_quote"),
            ("dropped_negation", "negation_dropped"),
            ("altered_number", "number_not_in_evidence"),
            ("unit_swap", "unit_mismatch"),
            ("wrong_anchor", "anchor_relocated"),
            ("may_as_asserted", "hedge_mismatch"),
            ("causal_overclaim", "causal_overclaim"),
            ("over_broad_assertion", "over_broad_assertion"),
        ]
        fault_summary: Dict[str, Any] = {}
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
            elif name == "unit_swap":
                c_mod["claims"][0]["statement"] = "At 573 K and 3.0 kPa, oxygen vacancies on Cu-In2O3 increase methanol selectivity from 72% to 91%."
                c_mod["claims"][0]["qualifiers"]["condition"] = "573 K, 3.0 kPa"
            elif name == "wrong_anchor":
                c_mod["claims"][3]["evidence"][0]["anchor"] = "p1_b03"
            elif name == "may_as_asserted":
                c_mod["claims"][1]["modality"] = "asserted"
            elif name == "causal_overclaim":
                c_mod["claims"][0]["predicate"] = "causes"
                c_mod["claims"][0]["evidence"][0]["quote"] = "Oxygen vacancies on Cu-In2O3 are associated with higher methanol selectivity."
                # Use an exact quote from paper or verify causal check against p1_b03
                c_mod["claims"][0]["evidence"][0]["quote"] = "Oxygen vacancies on Cu-In2O3 increase methanol selectivity from 72% to 91% at 573 K and 3.0 MPa."
                # Inject synthetic associational phrase on p1_b05 text via example_paper_assoc
            elif name == "over_broad_assertion":
                c_mod["claims"][0]["statement"] = (
                    "Oxygen vacancies on Cu-In2O3 increase methanol selectivity from 72% to 91% at 573 K and 3.0 MPa. "
                    "In addition, surface hydroxyl accumulation may induce partial sintering after 200 h and "
                    "carrier gas switching showed no significant difference across duplicate runs in the fixed-bed reactor."
                )

            if name == "causal_overclaim":
                assoc_in = tmp_path / "assoc_in"
                assoc_in.mkdir(parents=True, exist_ok=True)
                write_text_lf(assoc_in / "paper.md", "# Study\n\n## 1 Results\n\nOxygen vacancies are associated with higher methanol selectivity at 573 K.\n")
                shutil.copy2(in1 / "case.json", assoc_in / "case.json")
                run_prepare(assoc_in, assoc_in / "case.json", wf, of)
                c_mod["claims"] = [{
                    "id": "c_assoc",
                    "paper_id": "paper_1",
                    "statement": "Oxygen vacancies cause higher methanol selectivity at 573 K.",
                    "subject": "Oxygen vacancies",
                    "predicate": "causes",
                    "object": "higher methanol selectivity",
                    "polarity": "affirmative",
                    "modality": "asserted",
                    "evidence": [{"anchor": "p1_b03", "quote": "Oxygen vacancies are associated with higher methanol selectivity at 573 K."}],
                    "evidence_type": "direct_experimental",
                }]

            write_json_atomic(wf / "claims.json", c_mod)
            write_json_atomic(wf / "answer.json", a_mod)
            run_finalize(in1 / "case.json", wf, of)
            evf = json.loads((of / "evidence_check.json").read_text(encoding="utf-8"))
            all_codes = (
                [x.get("code") for x in evf.get("must_fix", [])]
                + [x.get("code") for x in evf.get("warnings", [])]
                + [x.get("type") for x in evf.get("change_records", [])]
            )
            detected = expected_code in all_codes
            fault_summary[name] = {
                "expected": expected_code,
                "detected": detected,
                "observed_codes": sorted(set(c for c in all_codes if c)),
            }

        results["tests"]["fault_injection"] = {
            "passed": all(v["detected"] for v in fault_summary.values()),
            "cases": fault_summary,
        }

        # 7. Encrypted PDF honest degradation
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
            "# Reading 14 校验器故障注入与 RDF 四图一致性实测记录",
            "",
            f"- 运行时间 (UTC)：`{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}`",
            f"- 测试脚本：`scripts/self_test.py`",
            f"- 全部测试通过：`{results['all_passed']}`",
            f"- RDF 导出校验：`{json.dumps(results['tests']['happy_closed_and_rdf']['rdf_validation'], ensure_ascii=False)}`",
            "",
            "| 测试项 / 注入故障 | 期望检出 / 验证目标 | 实测结果 | 观测细节 |",
            "|---|---|---|---|",
        ]
        for cname, cval in results["tests"]["fault_injection"]["cases"].items():
            lines.append(
                f"| fault:{cname} | `{cval['expected']}` | {'PASS' if cval['detected'] else 'FAIL'} | "
                f"`{', '.join(cval['observed_codes'])}` |"
            )
        lines.extend([
            f"| happy_closed_and_rdf | 契约零错 + 三格式 RDF 一致 | {'PASS' if results['tests']['happy_closed_and_rdf']['passed'] else 'FAIL'} | `exact={results['tests']['happy_closed_and_rdf']['counts']['exact']}` |",
            f"| nested_4graph_contract | 嵌套四图封闭契约投影 | {'PASS' if results['tests']['nested_4graph_contract']['passed'] else 'FAIL'} | `issues={len(results['tests']['nested_4graph_contract']['contract_issues'])}` |",
            f"| open_contract_no_clobber | 开放契约无覆盖 + 列表 claims | {'PASS' if results['tests']['open_contract_no_clobber']['passed'] else 'FAIL'} | `issues={len(results['tests']['open_contract_no_clobber']['contract_issues'])}` |",
            f"| multi_format_jats_multipaper_iri | JATS 表格 + 多篇元数据隔离 + 安全 IRI | {'PASS' if results['tests']['multi_format_jats_multipaper_iri']['passed'] else 'FAIL'} | `tables={results['tests']['multi_format_jats_multipaper_iri']['jats_tables']}, p2={results['tests']['multi_format_jats_multipaper_iri']['p2_title']}` |",
            f"| conflict_and_dedup | 去重 + 极性/反义谓词冲突检测 | {'PASS' if results['tests']['conflict_and_dedup']['passed'] else 'FAIL'} | `dedup={results['tests']['conflict_and_dedup']['dedup_count']}, conflicts={results['tests']['conflict_and_dedup']['conflict_types']}` |",
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
