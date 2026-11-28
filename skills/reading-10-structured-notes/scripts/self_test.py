"""End-to-end self-test, multi-format ingestion, and fault-injection benchmark for reading-10-structured-notes."""

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


def _make_docx(dest: Path) -> bool:
    try:
        import docx  # type: ignore
    except ImportError:
        return False
    d = docx.Document()
    d.core_properties.title = "Synthetic Catalysis DOCX"
    d.add_heading("1 Introduction", level=1)
    d.add_paragraph("Catalytic CO2 hydrogenation over Cu-In2O3 avoids rapid sintering.")
    d.add_heading("2 Experimental Results", level=1)
    tbl = d.add_table(rows=3, cols=3)
    headers = ["Catalyst", "Selectivity (%)", "Temp (K)"]
    rows = [["Cu-In2O3", "91.8", "573"], ["Cu-ZnO", "72.0", "573"]]
    for ci, h in enumerate(headers):
        tbl.cell(0, ci).text = h
    for ri, r in enumerate(rows, 1):
        for ci, v in enumerate(r):
            tbl.cell(ri, ci).text = v
    d.save(str(dest))
    return True


def run_suite(out_dir: Path, write_benchmark: bool = False) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    examples_dir = SKILL_DIR / "examples"
    case_path = examples_dir / "example_case.json"
    case_data = json.loads(case_path.read_text(encoding="utf-8-sig"))
    contract = case_data["output_contract"]

    results: Dict[str, Any] = {"skill": "reading-10-structured-notes", "tests": {}, "all_passed": False}

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        # 1. Happy-path closed contract + YAML frontmatter + real Paper Card anchors + notes_index.json
        w1 = tmp_path / "happy_work"
        o1 = tmp_path / "happy_out"
        in1 = tmp_path / "happy_in"
        in1.mkdir(parents=True, exist_ok=True)
        shutil.copy2(examples_dir / "example_paper.md", in1 / "example_paper.md")
        shutil.copy2(case_path, in1 / "case.json")
        run_prepare(in1, in1 / "case.json", w1, o1)
        shutil.copy2(examples_dir / "example_notes.json", w1 / "notes.json")
        shutil.copy2(examples_dir / "example_answer.json", w1 / "answer.json")
        rc = run_finalize(in1 / "case.json", w1, o1)
        res1 = json.loads((o1 / "result.json").read_text(encoding="utf-8"))
        ev1 = json.loads((o1 / "evidence_check.json").read_text(encoding="utf-8"))
        notes_md_txt = (o1 / "notes.md").read_text(encoding="utf-8")
        notes_idx = json.loads((o1 / "notes_index.json").read_text(encoding="utf-8"))
        brief_txt = (w1 / "case_brief.md").read_text(encoding="utf-8")
        issues1 = contract_issues(contract, res1)
        extra_keys = sorted(set(res1.keys()) - set(contract["properties"].keys()))
        happy_ok = (
            rc == 0
            and res1.get("case_id") == case_data["case_id"]
            and not issues1
            and not extra_keys
            and ev1["counts"]["unverified"] == 0
            and notes_md_txt.startswith("---\n")
            and "| key_findings |" in notes_md_txt
            and "| key_findings | " in notes_md_txt
            and "p.1 tab_1" in notes_md_txt
            and "Cu-In2O3" in (notes_idx.get("terms") or {})
            and "output_contract" in brief_txt
            and (w1 / "answer_template.json").is_file()
        )
        results["tests"]["happy_closed_contract"] = {
            "passed": happy_ok,
            "contract_issues": issues1,
            "extra_keys": extra_keys,
            "counts": ev1["counts"],
        }

        # 2. Open contract no-clobber + unwrapped answer.json
        w2 = tmp_path / "open_work"
        o2 = tmp_path / "open_out"
        in2 = tmp_path / "open_in"
        in2.mkdir(parents=True, exist_ok=True)
        shutil.copy2(examples_dir / "example_paper.md", in2 / "example_paper.md")
        open_case = {
            "case_id": "open-no-clobber",
            "input_files": ["example_paper.md"],
            "output_contract": {
                "type": "object",
                "required": ["case_id", "status", "evidence", "sources"],
                "properties": {
                    "case_id": {"type": "string"},
                    "status": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "object"}},
                    "sources": {"type": "array", "items": {"type": "string"}},
                },
            },
        }
        write_json_atomic(in2 / "case.json", open_case)
        run_prepare(in2, in2 / "case.json", w2, o2)
        shutil.copy2(examples_dir / "example_notes.json", w2 / "notes.json")
        write_json_atomic(w2 / "answer.json", {
            "case_id": "open-no-clobber",
            "status": "success",
            "evidence": [{"anchor": "tab_1", "quote": "| Cu-In2O3 | 573 | 3.0 | 91.8 | 14.2 |"}],
            "sources": ["example_paper.md (p.1)"],
        })
        run_finalize(in2 / "case.json", w2, o2)
        res2 = json.loads((o2 / "result.json").read_text(encoding="utf-8"))
        issues2 = contract_issues(open_case["output_contract"], res2)
        open_ok = (
            not issues2
            and isinstance(res2.get("evidence"), list)
            and len(res2["evidence"]) == 1
            and res2.get("sources") == ["example_paper.md (p.1)"]
        )
        results["tests"]["open_contract_no_clobber"] = {
            "passed": open_ok,
            "contract_issues": issues2,
        }

        # 3. Auto-projection from notes.json into closed paper_card/section_notes contract
        w3 = tmp_path / "proj_work"
        o3 = tmp_path / "proj_out"
        in3 = tmp_path / "proj_in"
        in3.mkdir(parents=True, exist_ok=True)
        shutil.copy2(examples_dir / "example_paper.md", in3 / "example_paper.md")
        proj_case = {
            "case_id": "proj-notes-test",
            "input_files": ["example_paper.md"],
            "output_contract": {
                "type": "object",
                "additionalProperties": False,
                "required": ["case_id", "status", "paper_card", "section_notes"],
                "properties": {
                    "case_id": {"type": "string"},
                    "status": {"type": "string", "enum": ["success", "partial", "unable"]},
                    "paper_card": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["title", "research_question", "method", "limitations"],
                        "properties": {
                            "title": {"type": "string"},
                            "research_question": {"type": "string"},
                            "method": {"type": "string"},
                            "limitations": {"type": "string"},
                        },
                    },
                    "section_notes": {"type": "array"},
                },
            },
        }
        write_json_atomic(in3 / "case.json", proj_case)
        run_prepare(in3, in3 / "case.json", w3, o3)
        shutil.copy2(examples_dir / "example_notes.json", w3 / "notes.json")
        run_finalize(in3 / "case.json", w3, o3)
        res3 = json.loads((o3 / "result.json").read_text(encoding="utf-8"))
        issues3 = contract_issues(proj_case["output_contract"], res3)
        proj_ok = (
            not issues3
            and bool(res3.get("paper_card", {}).get("research_question"))
            and len(res3.get("section_notes") or []) == 3
        )
        results["tests"]["auto_projection_from_notes"] = {
            "passed": proj_ok,
            "contract_issues": issues3,
        }

        # 4. Multi-format ingestion (DOCX + JATS + CSV) + inline tables
        w4 = tmp_path / "mf_work"
        o4 = tmp_path / "mf_out"
        in4 = tmp_path / "mf_in"
        in4.mkdir(parents=True, exist_ok=True)
        docx_ok = _make_docx(in4 / "manuscript.docx")
        write_text_lf(in4 / "study.jats", (
            "<article><front><article-meta><title-group><article-title>JATS Study</article-title></title-group>"
            "<article-id pub-id-type='doi'>10.1000/jats.1</article-id><pub-date><year>2025</year></pub-date>"
            "</article-meta></front><body><sec><title>Methods</title><p id='p1'>Tested at 12 Hz.</p></sec></body></article>"
        ))
        write_text_lf(in4 / "metrics.csv", "Model,F1,Latency_ms\nOurs,0.91,14\nBase,0.78,22\n")
        write_json_atomic(in4 / "case.json", {"case_id": "mf-test"})
        mf_meta = run_prepare(in4, in4 / "case.json", w4, o4)
        mf_titles = [p["title"] for p in mf_meta["papers"]]
        mf_idx = json.loads((w4 / "index.json").read_text(encoding="utf-8"))
        mf_tables = [e for e in mf_idx if e["kind"] == "table"]
        mf_ok = (
            len(mf_meta["papers"]) >= (3 if docx_ok else 2)
            and any("JATS" in t for t in mf_titles)
            and len(mf_tables) >= (2 if docx_ok else 1)
            and all(p["outline"] for p in mf_meta["papers"])
        )
        results["tests"]["multi_format_docx_jats_csv"] = {
            "passed": mf_ok,
            "papers_count": len(mf_meta["papers"]),
            "tables_indexed": len(mf_tables),
        }

        # 5. Cross-block continuation exact match
        w5 = tmp_path / "cont_work"
        o5 = tmp_path / "cont_out"
        in5 = tmp_path / "cont_in"
        in5.mkdir(parents=True, exist_ok=True)
        write_json_atomic(in5 / "case.json", {"case_id": "cont-test"})
        run_prepare(in1, in1 / "case.json", w5, o5)
        cont_idx = [
            {
                "paper_id": "paper_1", "anchor": "p1_b10", "kind": "block", "page": 1,
                "section_id": "sec_1", "section_title": "1 Intro",
                "text": "Furthermore, batch normaliza-", "continuation_of": None, "continued_by": "p2_b01",
            },
            {
                "paper_id": "paper_1", "anchor": "p2_b01", "kind": "block", "page": 2,
                "section_id": "sec_1", "section_title": "1 Intro",
                "text": "tion cannot be applied to online learning tasks.", "continuation_of": "p1_b10", "continued_by": None,
            },
        ]
        write_json_atomic(w5 / "index.json", cont_idx)
        from evidence import verify as _verify
        cont_res = _verify(
            "batch normalization cannot be applied to online learning tasks.",
            "p2_b01",
            "paper_1",
            cont_idx,
        )
        results["tests"]["cross_block_continuation"] = {
            "passed": cont_res["match"] == "exact" and cont_res.get("span_anchors") == ["p1_b10", "p2_b01"],
            "match": cont_res["match"],
            "span_anchors": cont_res.get("span_anchors"),
        }

        # 6. Fault injection checks (including unit_mismatch)
        fault_cases = [
            ("altered_quote", "unverified_quote"),
            ("dropped_negation", "negation_dropped"),
            ("altered_number", "number_not_in_evidence"),
            ("unit_swap", "unit_mismatch"),
            ("wrong_anchor", "anchor_relocated"),
        ]
        fault_summary: Dict[str, Any] = {}
        base_notes = json.loads((examples_dir / "example_notes.json").read_text(encoding="utf-8-sig"))
        base_answer = json.loads((examples_dir / "example_answer.json").read_text(encoding="utf-8-sig"))

        for name, expected_code in fault_cases:
            wf = tmp_path / f"work_{name}"
            of = tmp_path / f"out_{name}"
            run_prepare(in1, in1 / "case.json", wf, of)
            n_mod = copy.deepcopy(base_notes)
            a_mod = copy.deepcopy(base_answer)

            if name == "altered_quote":
                n_mod["papers"][0]["sections"][0]["points"][0]["evidence"][0]["quote"] = (
                    "conventional Cu-ZnO catalysts suffer from instant evaporation under high pressure."
                )
            elif name == "dropped_negation":
                n_mod["papers"][0]["sections"][2]["points"][1]["text"] = (
                    "10 vol% 共进水导致 Cu-In2O3 不可逆失活，Cu-ZnO 损失 28% 活性。"
                )
            elif name == "altered_number":
                n_mod["papers"][0]["sections"][2]["points"][0]["text"] = (
                    "在 573 K 与 3.0 MPa 下，Cu-In2O3 的甲醇选择性为 99.4%、CO2 转化率为 14.2%。"
                )
            elif name == "unit_swap":
                n_mod["papers"][0]["sections"][1]["points"][0]["text"] = (
                    "固定床反应条件为 573 K、3.0 kPa、GHSV = 12000 mL g^-1 h^-1、H2/CO2 = 3:1、运行时长 100 h。"
                )
            elif name == "wrong_anchor":
                n_mod["papers"][0]["sections"][2]["points"][1]["evidence"][0]["anchor"] = "p1_b03"

            write_json_atomic(wf / "notes.json", n_mod)
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
            "case_id": "enc-test",
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
            res_enc.get("case_id") == "enc-test"
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
            "# Reading 10 校验器故障注入与端到端实测记录",
            "",
            f"- 运行时间 (UTC)：`{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}`",
            f"- 测试脚本：`scripts/self_test.py`",
            f"- 全部测试通过：`{results['all_passed']}`",
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
            f"| happy_closed_contract | 契约零错 + Frontmatter + 真实锚点 | {'PASS' if results['tests']['happy_closed_contract']['passed'] else 'FAIL'} | `exact={results['tests']['happy_closed_contract']['counts']['exact']}` |",
            f"| open_contract_no_clobber | 开放契约无覆盖 + 非包装 answer.json | {'PASS' if results['tests']['open_contract_no_clobber']['passed'] else 'FAIL'} | `issues={len(results['tests']['open_contract_no_clobber']['contract_issues'])}` |",
            f"| auto_projection_from_notes | 笔记自动投影封闭契约 | {'PASS' if results['tests']['auto_projection_from_notes']['passed'] else 'FAIL'} | `issues={len(results['tests']['auto_projection_from_notes']['contract_issues'])}` |",
            f"| multi_format_docx_jats_csv | DOCX/JATS/CSV 多格式与内联表 | {'PASS' if results['tests']['multi_format_docx_jats_csv']['passed'] else 'FAIL'} | `papers={results['tests']['multi_format_docx_jats_csv']['papers_count']}, tables={results['tests']['multi_format_docx_jats_csv']['tables_indexed']}` |",
            f"| cross_block_continuation | 跨页断词续接引文精确匹配 | {'PASS' if results['tests']['cross_block_continuation']['passed'] else 'FAIL'} | `match={results['tests']['cross_block_continuation']['match']}` |",
            f"| encrypted_pdf_fallback | 状态降级为 unable/partial | {'PASS' if results['tests']['encrypted_pdf_fallback']['passed'] else 'FAIL'} | `status={results['tests']['encrypted_pdf_fallback']['status']}` |",
        ])
        write_text_lf(bench_dir / "validator_benchmark.md", "\n".join(lines) + "\n")

    print(json.dumps(results, ensure_ascii=False, indent=2))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Run reading-10 self-test suite.")
    parser.add_argument("--out-dir", default="/tmp/reading10-selftest", help="Directory to write test report")
    parser.add_argument("--write-benchmark", action="store_true", help="Update benchmarks/validator_benchmark.md")
    args = parser.parse_args()
    res = run_suite(Path(args.out_dir), write_benchmark=args.write_benchmark)
    return 0 if res["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
