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


def render_parameter_table_md(param_table: Dict[str, Any], is_zh: bool = False) -> str:
    lines: List[str] = [
        "# 参数与来源对照表 (Parameter Provenance Table)" if is_zh else "# Parameters & Provenance Table",
        "",
        "| 参数名 (Name) | 取值 (Value) | 单位 (Unit) | 作用对象 (Target) | 证据来源 (Source) |"
        if is_zh
        else "| Name | Value | Unit | Target Component | Source Provenance |",
        "|---|---|---|---|---|",
    ]
    for p in param_table.get("parameters", []):
        lines.append(
            f"| `{p['name']}` | `{p['value']}` | `{p['unit']}` | {p['target_component']} | `{p['source']}` |"
        )
    if not param_table.get("parameters"):
        lines.append("| `[NONE]` | - | - | - | No parameters extracted |")
    lines.append("")
    return "\n".join(lines)


def render_repro_and_questions_md(facts: Dict[str, Any], is_zh: bool = False) -> str:
    setup = facts.get("setup", {})
    lines: List[str] = [
        "# 复现清单与待作者确认项 (Reproducibility & Author Questions)" if is_zh else "# Reproducibility Checklist & Author Questions",
        "",
        "## 复现配置清单" if is_zh else "## Reproducibility Checklist",
        f"- **Software:** {setup.get('software') or '[MISSING - author confirmation required]'}",
        f"- **Hardware:** {setup.get('hardware') or '[MISSING - author confirmation required]'}",
        f"- **Seeds:** {setup.get('seeds') or '[MISSING - author confirmation required]'}",
        f"- **Repeats:** {setup.get('repeats') or '[MISSING - author confirmation required]'}",
        f"- **Command:** `{setup.get('command') or 'MISSING'}`",
        "",
        "## 跨文件参数冲突" if is_zh else "## Cross-Source Parameter Conflicts",
    ]
    if facts.get("conflicts"):
        for cf in facts["conflicts"]:
            lines.append(f"- **[CONFLICT] `{cf['parameter']}`**: {cf['message']}")
    else:
        lines.append("- None detected." if not is_zh else "- 未检出跨文件参数冲突。")
    lines.extend(["", "## 需作者确认的问题" if is_zh else "## Open Author Questions"])
    if facts.get("author_questions"):
        for q in facts["author_questions"]:
            lines.append(f"- **`{q['field']}`**: {q['question']}")
    else:
        lines.append("- None." if not is_zh else "- 无待确认缺口。")
    lines.append("")
    return "\n".join(lines)


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
        param_summary = "; ".join(f"`{p['name']}` = `{p['value']}` ({p['unit']}, source: `{p['source']}`)" for p in params)
        lines.append(f"All experimental parameters are traceably synchronized with source artifacts: {param_summary}.")
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
                f"preprocessing={ds.get('preprocessing', 'none')}."
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
