#!/usr/bin/env python3
"""Draft editable 6-section Methods & Experiments chapter, parameter table, and repro checklist."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from exp_utils import write_text_lf


def display(value, missing="—"):
    if value in (None, "", []):
        return missing
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def cell(value):
    return display(value).replace("|", "\\|").replace("\n", " ")


def render_parameter_table_md(param_table: Dict[str, Any], is_zh: bool = False) -> str:
    title = "参数、适用条件与出处" if is_zh else "Parameter values, scope and provenance"
    headers = ("参数名", "取值", "单位", "作用对象", "原始材料与位置") if is_zh else (
        "Parameter", "Value", "Unit", "Applies to", "Source and location")
    lines = [f"# {title}", "", "| " + " | ".join(headers) + " |", "|---|---|---|---|---|"]
    for param in param_table.get("parameters", []):
        observations = param.get("observations", []) or [param]
        for obs in observations:
            unresolved = param.get("status") == "unresolved"
            note = ("（待确认）" if is_zh else " (unresolved)") if unresolved else ""
            source = display(obs.get("source", param.get("source")))
            location = display(obs.get("locator", param.get("locator")), "")
            fields = [param["name"], display(obs.get("value", param.get("value"))) + note,
                      obs.get("unit") or param.get("unit"), obs.get("target_component") or param.get("target_component"),
                      source + " " + location]
            lines.append("| " + " | ".join(cell(v) for v in fields) + " |")
    if not param_table.get("parameters"):
        lines.extend(["", "材料中未抽取到可定位参数。" if is_zh else "No parameter with a source was extracted."])
    return "\n".join(lines) + "\n"


def render_repro_and_questions_md(facts: Dict[str, Any], is_zh: bool = False) -> str:
    setup = facts.get("setup", {})
    lines = ["# " + ("复现清单与作者确认项" if is_zh else "Reproducibility checklist and open questions"), ""]
    labels = [("software", "软件版本", "Software"), ("hardware", "硬件或仪器", "Hardware"),
              ("seeds", "随机种子", "Seeds"), ("repeats", "独立重复", "Repeats"),
              ("code_version", "代码版本", "Code revision"), ("command", "执行命令或操作规程", "Command or protocol"),
              ("data_access", "数据来源与获取方式", "Data access")]
    missing = "材料未说明" if is_zh else "Not reported in the materials"
    conflicts = {row.get("parameter") for row in facts.get("conflicts", [])}
    for key, zh, en in labels:
        gap = ("来源取值有分歧，见下文" if is_zh else "Sources disagree; see discrepancies below") if "setup." + key in conflicts else missing
        references = [row["source"] + " " + row["locator"] for row in facts.get("setup_evidence", {}).get(key, [])]
        suffix = " (" + ", ".join(dict.fromkeys(references)) + ")" if references else ""
        lines.append(f"- **{zh if is_zh else en}** — {display(setup.get(key), gap)}{suffix}")
    for dataset in facts.get("datasets", []):
        if isinstance(dataset, dict):
            source = dataset.get("evidence_source") or dataset.get("source")
            locator = dataset.get("evidence_locator") or dataset.get("locator")
            suffix = f" ({source} {locator})" if source and locator else ""
            lines.append(f"- **{display(dataset.get('name'))}** — {display(dataset.get('version'), missing)}{suffix}")
    for title, items in [
        ("跨来源冲突" if is_zh else "Discrepancies", [c.get("message") or display(c.get("values")) for c in facts.get("conflicts", [])]),
        ("待作者确认" if is_zh else "Questions for the author", [q.get("question") if isinstance(q, dict) else q for q in facts.get("author_questions", [])]),
    ]:
        if items:
            lines.extend(["", "## " + title, *["- " + display(item) for item in items]])
    return "\n".join(lines) + "\n"


def render_methods_chapter(facts: Dict[str, Any], param_table: Dict[str, Any], *, language: str = "en") -> str:
    """Draft a verifiable 6-section Methods & Experiments chapter aligned 1-to-1 to facts."""
    is_zh = language.lower().startswith("zh")
    setup = facts.get("setup", {})
    steps = facts.get("steps", [])
    datasets = facts.get("datasets", [])
    baselines = facts.get("baselines", [])
    limitations = facts.get("limitations", [])
    params = param_table.get("parameters", [])

    title = facts.get("title") or ("方法与实验章节" if is_zh else "Methods and Experiments")
    lines: List[str] = [f"# {title}", ""]

    # 1. Method Steps
    lines.append("## 方法步骤" if is_zh else "## Method Steps")
    if steps:
        for idx, st in enumerate(steps, start=1):
            if isinstance(st, dict):
                lines.append(
                    f"{idx}. **{st.get('name', f'Step {idx}')}** — "
                    f"Input: {st.get('input', '-')} | Operation: {st.get('operation', '-')} | "
                    f"Output: {st.get('output', '-')} | Stop/Criterion: {st.get('criterion', '-')}"
                )
            else:
                lines.append(f"{idx}. {st}")
    else:
        lines.append("[Material gap: structured procedural steps not provided.]")
    lines.append("")

    # 2. Experimental Setup (strict train/val/test or protocol separation)
    lines.append("## 实验设置" if is_zh else "## Experimental Setup")
    lines.append(f"- **Software & Environment:** {setup.get('software', '[UNSPECIFIED]')}")
    lines.append(f"- **Hardware:** {setup.get('hardware', '[UNSPECIFIED]')}")
    lines.append(f"- **Training / Synthesis Protocol:** {setup.get('train_protocol', '[UNSPECIFIED]')}")
    lines.append(f"- **Validation / Calibration Protocol:** {setup.get('val_protocol', '[UNSPECIFIED]')}")
    lines.append(f"- **Test / Evaluation Protocol:** {setup.get('test_protocol', '[UNSPECIFIED]')}")
    lines.append(f"- **Random Seeds & Repeats:** seeds={setup.get('seeds', '[UNSPECIFIED]')}, repeats={setup.get('repeats', '[UNSPECIFIED]')}")
    lines.append("")

    # 3. Parameters & Provenance
    lines.append("## 参数与来源" if is_zh else "## Parameters & Provenance")
    if params:
        param_summary = "; ".join(f"`{p['name']}` = `{display(p['value'])}` ({display(p.get('unit'))}, source: `{p.get('source', '')} {p.get('locator', '')}`)" for p in params if p.get('value') is not None)
        lines.append(f"{param_summary}")
    else:
        lines.append("[No parameters found across input configs/logs/code.]")
    if facts.get("conflicts"):
        lines.append("")
        lines.append("**[CONFLICT DISCLOSURE / 参数冲突提示]:**")
        for cf in facts["conflicts"]:
            lines.append(f"- Conflict on `{cf['parameter']}`: {cf['values']} (requires author confirmation before submission).")
    lines.append("")

    # 4. Datasets & Splits
    lines.append("## 数据集与划分" if is_zh else "## Datasets & Splits")
    if datasets:
        for ds in datasets:
            lines.append(
                f"- **{ds.get('name', 'Dataset')}** (version `{ds.get('version', 'UNSPECIFIED')}` | size: {ds.get('size', 'UNSPECIFIED')}): "
                f"splits={ds.get('splits', 'UNSPECIFIED')}; split_method={ds.get('split_method', 'UNSPECIFIED')}; "
                f"preprocessing={ds.get('preprocessing', 'UNSPECIFIED')}."
            )
    else:
        lines.append("[Material gap: dataset/sample specification not provided.]")
    lines.append("")

    # 5. Baselines & Controls
    lines.append("## 对照实验" if is_zh else "## Baselines & Controls")
    if baselines:
        for bl in baselines:
            lines.append(
                f"- **{bl.get('name', 'Baseline')}** (version `{bl.get('version', 'UNSPECIFIED')}`): "
                f"matched_budget={bl.get('matched_budget', 'UNSPECIFIED')}; metric={bl.get('metric', 'UNSPECIFIED')}; "
                f"stats={bl.get('statistical_test', 'UNSPECIFIED')}."
            )
    else:
        lines.append("[Material gap: baseline/control comparisons not provided.]")
    lines.append("")

    # 6. Reproducibility & Limitations
    lines.append("## 可复现性与限制条件" if is_zh else "## Reproducibility & Limitations")
    lines.append(f"- **Execution Command:** `{setup.get('command', '[UNSPECIFIED]')}`")
    lines.append(f"- **Scanned Source Artifacts:** `{', '.join(facts.get('scanned_sources', [])) or 'None'}`")
    if limitations:
        for lim in limitations:
            lines.append(f"- **Failure Mode / Scope Limit:** {lim}")
    else:
        lines.append("- **Failure Mode / Scope Limit:** [Author confirmation required for concrete failure boundaries; missing items: " + (", ".join(facts.get("missing_repro_fields", [])) or "none") + "].")
    lines.append("")

    if not steps and facts.get("evidence"):
        lines.extend(["## " + ("待编入正文的材料摘录" if is_zh else "Source excerpts for drafting"), ""])
        seen = set()
        for row in facts["evidence"]:
            if row["source"] in seen:
                continue
            seen.add(row["source"])
            lines.append(f"- {row['source']} {row['locator']} — {row['text'][:350]}")
            if len(seen) == 8:
                break
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Draft editable Methods & Experiments chapter.")
    parser.add_argument("--facts", required=True)
    parser.add_argument("--params", required=True)
    parser.add_argument("--language", default="en")
    parser.add_argument("--out-chapter", required=True)
    parser.add_argument("--out-table", required=True)
    parser.add_argument("--out-repro", required=True)
    args = parser.parse_args()

    facts = json.loads(Path(args.facts).read_text(encoding="utf-8"))
    params = json.loads(Path(args.params).read_text(encoding="utf-8"))
    is_zh = args.language.lower().startswith("zh")

    write_text_lf(Path(args.out_chapter), render_methods_chapter(facts, params, language=args.language))
    write_text_lf(Path(args.out_table), render_parameter_table_md(params, is_zh=is_zh))
    write_text_lf(Path(args.out_repro), render_repro_and_questions_md(facts, is_zh=is_zh))
    print(json.dumps({"chapter": str(args.out_chapter), "parameters": params["parameter_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
