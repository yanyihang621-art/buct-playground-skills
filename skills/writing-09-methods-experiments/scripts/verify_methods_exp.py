#!/usr/bin/env python3
"""6-check deterministic verifier for Methods & Experiments chapters."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from exp_utils import load_methods_rules, write_json_atomic


def verify_methods_and_experiments(
    chapter_md: str,
    repro_md: str,
    facts: Dict[str, Any],
    param_table: Dict[str, Any],
    *,
    status_claimed: str = "completed",
    language: str = "en",
    rules_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Verify completeness, parameter-prose consistency, conflicts, separation, baselines, and limitations."""
    rules = load_methods_rules(rules_path)
    is_zh = language.lower().startswith("zh")
    violations: List[Dict[str, Any]] = []

    # 1. Six-element completeness
    req_secs = rules.get("required_sections_zh" if is_zh else "required_sections_en", [])
    missing_secs = [s for s in req_secs if not re.search(rf"^#{{1,3}}\s*{re.escape(s)}", chapter_md, re.MULTILINE | re.IGNORECASE)]
    six_ok = (len(missing_secs) == 0)
    if not six_ok:
        violations.append({"check": "six_element_completeness", "missing_sections": missing_secs})

    # 2. Parameter-prose consistency (every extracted parameter name & exact value must appear in prose)
    params = param_table.get("parameters", [])
    matched_params = 0
    for p in params:
        nm = str(p["name"])
        val = str(p["value"])
        # Check name present and exact value substring present nearby or in section
        if nm in chapter_md and val in chapter_md:
            matched_params += 1
        else:
            violations.append(
                {
                    "check": "parameter_prose_consistency",
                    "parameter": nm,
                    "expected_value": val,
                    "source": p.get("source"),
                    "reason": "missing_or_mismatched_value_in_prose",
                }
            )
    param_ratio = 1.0 if not params else round(matched_params / len(params), 4)
    param_ok = (matched_params == len(params)) and (len(params) > 0)

    # 3. Cross-source conflict disclosure & honest status
    conflicts = facts.get("conflicts", [])
    conflict_ok = True
    for cf in conflicts:
        pname = str(cf.get("parameter", ""))
        if pname not in chapter_md or ("CONFLICT" not in chapter_md.upper() and "冲突" not in chapter_md):
            conflict_ok = False
            violations.append({"check": "cross_source_conflict_disclosure", "parameter": pname, "reason": "suppressed_conflict_in_prose"})
    if conflicts and status_claimed == "completed":
        conflict_ok = False
        violations.append({"check": "cross_source_conflict_disclosure", "reason": "unresolved_conflict_cannot_claim_completed"})

    # 4. Train / Val / Test separation & seeds/dataset_version coverage
    setup = facts.get("setup", {})
    datasets = facts.get("datasets", [])
    has_split_sep = bool(setup.get("train_protocol") and setup.get("test_protocol"))
    missing_repro = facts.get("missing_repro_fields", [])
    sep_ok = has_split_sep and (len(datasets) > 0)
    if not sep_ok:
        violations.append({"check": "train_val_test_separation_and_seeds", "has_split_sep": has_split_sep, "dataset_count": len(datasets)})
    if missing_repro and status_claimed == "completed":
        sep_ok = False
        violations.append({"check": "train_val_test_separation_and_seeds", "missing_repro_fields": missing_repro})

    # 5. Baseline fairness & reproducibility checklist
    baselines = facts.get("baselines", [])
    base_ok = (len(baselines) > 0) and bool(repro_md.strip())
    if not base_ok:
        violations.append({"check": "baseline_fairness_and_repro_checklist", "baseline_count": len(baselines)})

    # 6. Concrete limitations (anti-cliche) & honesty
    cliche_hits = [pat for pat in rules.get("banned_generic_limitations", []) if re.search(pat, chapter_md, re.IGNORECASE)]
    limitations = facts.get("limitations", [])
    lim_ok = (not cliche_hits) and (len(limitations) > 0 or bool(missing_repro))
    if not lim_ok:
        violations.append({"check": "concrete_limitations_and_honesty", "cliche_hits": cliche_hits, "limitations_count": len(limitations)})

    checks = {
        "six_element_completeness": six_ok,
        "parameter_prose_consistency": param_ok,
        "cross_source_conflict_disclosure": conflict_ok,
        "train_val_test_separation_and_seeds": sep_ok,
        "baseline_fairness_and_repro_checklist": base_ok,
        "concrete_limitations_and_honesty": lim_ok,
    }
    passed = all(checks.values())

    return {
        "schema_version": "writing-09-qa/v1",
        "passed": passed,
        "metrics": {
            "parameter_count": len(params),
            "conflict_count": len(conflicts),
            "step_count": len(facts.get("steps", [])),
            "dataset_count": len(datasets),
            "baseline_count": len(baselines),
            "missing_repro_count": len(missing_repro),
            "parameter_prose_consistency": param_ratio,
        },
        "checks": checks,
        "violations": violations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify drafted Methods & Experiments chapter.")
    parser.add_argument("--chapter", required=True)
    parser.add_argument("--repro", required=True)
    parser.add_argument("--facts", required=True)
    parser.add_argument("--params", required=True)
    parser.add_argument("--status", default="completed")
    parser.add_argument("--language", default="en")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    ch = Path(args.chapter).read_text(encoding="utf-8")
    rp = Path(args.repro).read_text(encoding="utf-8")
    fc = json.loads(Path(args.facts).read_text(encoding="utf-8"))
    pr = json.loads(Path(args.params).read_text(encoding="utf-8"))
    rep = verify_methods_and_experiments(ch, rp, fc, pr, status_claimed=args.status, language=args.language)
    write_json_atomic(Path(args.out), rep)
    print(json.dumps({"passed": rep["passed"], "checks": rep["checks"], "violations": len(rep["violations"])}, ensure_ascii=False))
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
