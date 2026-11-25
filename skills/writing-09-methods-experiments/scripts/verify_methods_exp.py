#!/usr/bin/env python3
"""Verify the editable methods chapter against the experiment ledger."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from exp_utils import load_methods_rules, write_json_atomic
from parameter_claims import ParameterNames, inspect_assignments, manuscript_clauses, plain, value_present


# These are section meanings, not required literal titles or a required ordering.
TOPICS = {
    "steps": r"procedur|protocol|method|workflow|process|方法|步骤|工艺|操作|实施|流程|制备",
    "setup": r"experimental|setup|apparatus|equipment|environment|design|setting|实验|设置|装置|环境|仪器|设计",
    "parameters": r"parameter|hyperparameter|setting|condition|dose|measurement|参数|设置|条件|剂量|测量",
    "datasets": r"dataset|splits?|cohort|samples?|population|specimen|participants?|materials|数据|划分|样品|样本|材料|受试|队列|试件",
    "baselines": r"baseline|control|compar(?:ison|ator)|ablation|对照|消融|基线|比较|对比",
    "limits": r"reproduc|limitation|scope|uncertaint|boundar|repeatab|限制|局限|复现|适用|边界|不确定",
}
GAP = r"not (?:reported|provided|recorded|available|specified|applicable)|unreported|unspecified|missing|material gap|confirm|unknown|unavailable|未(?:记录|说明|报告|提供|披露|指定|知)|待(?:核实|确认|补充)|缺(?:失|少|口)|不适用|无法(?:确认|判断)"
CONFLICT = r"conflict|differ(?:ent|s|ed)?|discrepan|mismatch|inconsisten|override|whereas|versus|\bvs\.?\b|冲突|不同|不一[致同]|不符|覆盖|分歧|待确认"
HEAD = re.compile(r"^\s*#{1,6}\s+(.+)$")
PHASES = {
    "train_protocol": r"\btrain(?:ing)?\b|fit(?:ting)?\b|prepar(?:ation|ed)|synthesis|训练|拟合|制备|合成",
    "val_protocol": r"\bval(?:idat(?:ion|ed|ing|e))?\b|calibrat|验证|校准|校正|标定|复校",
    "test_protocol": r"\btest(?:ing|ed)?\b|evaluat|measur|assay|测(?:试|量|定)|评价|评估",
}
TEST_GROUP = r"(?:\btest(?:ing)?\s+(?:set|split|fold|data|cohort|samples?)\b|测试(?:数据|集|组|样本))"
TUNING = r"(?:\b(?:tun(?:e|ing|ed)|model selection|threshold selection|hyperparameter selection)\b|(?:调参|调优|选择(?:模型|阈值|超参数)))"
NEGATION = r"\b(?:not|never|without|excluded|no)\b|未(?:曾|被)?|不(?:得|曾|被|参与|用于|使用|作)|禁止|排除"
TEST_FOR_TUNING = re.compile(
    TEST_GROUP + r"[^.;。；\n]{0,50}?(?:used|employed|用于|参与|用来|without)\s*[^.;。；\n]{0,32}?" + TUNING +
    r"|" + r"(?:used?|using|employed?|使用|利用|用)\s*(?:the\s+|独立的?)?" + TEST_GROUP +
    r"\s*(?:to|for|以|来|进行|做|用于)\s*(?:hyperparameter\s+)?" + TUNING +
    r"|" + TUNING + r"\s+(?:on|using|with)\s+(?:the\s+)?" + TEST_GROUP, re.I)
FIELD_WORDS = {"software": "software|软件", "hardware": "hardware|硬件", "seeds": "seeds?|随机种子",
               "repeats": "repeat|replicat|重复", "command": "command|命令", "dataset_version": "version|版本"}


def sections(text):
    title, body = "", []
    result = []
    for line in plain(text).splitlines():
        heading = HEAD.match(line)
        if heading:
            result.append((title, "\n".join(body)))
            title, body = heading[1], []
        else:
            body.append(line)
    result.append((title, "\n".join(body)))
    return result


def text_content(text):
    """Exclude empty markup and table dividers from substantive-content checks."""
    return bool(re.search(r"[a-zA-Z0-9\u3400-\u9fff]{2}", text))


def row_names(records):
    return [str(r["name"]) for r in records if isinstance(r, dict) and r.get("name")]


def includes(text, terms):
    return any(term and str(term).casefold() in text.casefold() for term in terms)


def procedure_matches(prose, steps):
    # Cases often name sections after the operation. Look for the sourced action
    # and an associated material/object even when the author conjugates its name.
    def keywords(value):
        words = re.findall(r"[a-z]{3,}|[\u3400-\u9fff]{2,}", str(value).casefold())
        return [re.sub(r"(?:ing|ed|s)$", "", w) for w in words
                if w not in {"the", "and", "with", "after", "before", "using", "were", "was", "then", "into", "from", "each"}]

    for step in steps:
        if not isinstance(step, dict) or not step.get("operation"):
            continue
        operation = keywords(step["operation"])
        if not operation:
            continue
        for line in prose.splitlines():
            words = set(keywords(line))
            if operation[0] in words and len(set(operation) & words) >= min(2, len(operation)):
                return True
    return False


def changed_negation(prose, source_statements):
    """Catch a directly reversed source statement; unfamiliar paraphrases need semantic review."""
    negation = re.compile(r"\b(?:no|not|never|without)\b|并未|没有|不曾|未曾|未|不", re.I)

    def signature(sentence):
        lower = negation.sub("", sentence.casefold())
        tokens = re.findall(r"[a-z]+|[0-9.]+|[\u3400-\u9fff]", lower)
        return tuple(w for w in tokens if w not in {"a", "an", "the", "do", "does", "did", "."})

    propositions = {}
    for source in source_statements:
        for statement in re.split(r"[。!！?？;；]|\.\s|[\r\n]", plain(source)):
            key = signature(statement)
            if len(key) >= 3:
                propositions.setdefault(key, set()).add(bool(negation.search(statement)))
    errors = []
    for i, line in enumerate(plain(prose).splitlines(), 1):
        for statement in re.split(r"[。!！?？;；]|\.\s", line):
            key = signature(statement)
            known = propositions.get(key, set())
            if len(known) == 1 and bool(negation.search(statement)) not in known:
                errors.append({"reason": "source_statement_negation_reversed", "line": i, "excerpt": statement[:220]})
    return errors


def same_line(text, first, second):
    return any(re.search(first, line, re.I) and re.search(second, line, re.I) for line in text.splitlines())


def conflict_disclosures(chapter, facts, status, params=()):
    errors = []
    clauses = list(manuscript_clauses(chapter))
    conflicts = facts.get("conflicts", [])
    names = ParameterNames([str(row.get("name", "")) for row in params] +
                           [str(issue.get("parameter", "")) for issue in conflicts])
    for conflict in conflicts:
        name = str(conflict.get("parameter", ""))
        values = conflict.get("values", {})
        if not name or not isinstance(values, dict) or not values:
            errors.append({"reason": "missing_conflict_evidence", "parameter": name})
            continue
        disclosed = False
        for i, (line_no, clause, heading) in enumerate(clauses):
            if any(names.matches(name, clause, heading)):
                nearby = [clause]
                for next_no, continuation, next_heading in clauses[i+1:i+4]:
                    if next_heading != heading or next_no > line_no + 1:
                        break
                    # A second parameter/section starts a different disclosure.
                    if any(pattern.search(continuation) for pattern in names.patterns.values()):
                        break
                    nearby.append(continuation)
                    line_no = next_no
                window = "\n".join(nearby)
                if not re.search(CONFLICT, window + "\n" + heading, re.I):
                    continue
                if all(value_present(val, window) for val in values.values()):
                    disclosed = True
                    break
                if re.search(r"parameter table|conflict table|provenance table|参数表|冲突表|来源表", window, re.I):
                    disclosed = True
                    break
        if not disclosed:
            errors.append({"reason": "conflict_requires_values_or_reference_to_conflict_table",
                           "parameter": name, "sources": list(values)})
    if facts.get("conflicts") and status == "completed":
        errors.append({"reason": "unresolved_conflict_cannot_claim_completed"})
    return errors


def inspect_datasets(text, datasets):
    issues = []
    for dataset in datasets:
        if not isinstance(dataset, dict) or not dataset.get("name"):
            continue
        name = str(dataset["name"])
        expected_version = str(dataset.get("version") or "")
        expected_split = re.findall(r"\d+(?:\.\d+)?", str(dataset.get("splits") or ""))
        for num, line in enumerate(plain(text).splitlines(), 1):
            if name.casefold() not in line.casefold():
                continue
            if sum(n.casefold() in line.casefold() for n in row_names(datasets)) != 1:
                continue
            version = re.search(re.escape(name) + r"\s*[(,（]?\s*(?:version|版本)\s*[:=]?\s*(v?\d[\w.-]*)", line, re.I)
            if expected_version and version and version[1].rstrip(".").casefold().removeprefix("v") != expected_version.casefold().removeprefix("v"):
                issues.append({"reason": "dataset_version_disagrees", "dataset": name, "expected": expected_version,
                               "observed": version[1], "line": num})
            # Require an explicit ratio in BOTH source and prose; ignore ratios in sizes or dates.
            ratio = re.search(r"(?:splits?|划分(?:比例)?)[\s=:为]*((?:\d+(?:\.\d+)?%?\s*[/：:]\s*){1,3}\d+(?:\.\d+)?%?)", line, re.I)
            if ratio and re.fullmatch(r"\s*(?:\d+(?:\.\d+)?%?\s*[/：:]\s*){1,3}\d+(?:\.\d+)?%?\s*", str(dataset.get("splits") or "")):
                observed = re.findall(r"\d+(?:\.\d+)?", ratio[1])
                # 8:1:1 and 80/10/10 represent the same shares.
                a, b = [float(v) for v in expected_split], [float(v) for v in observed]
                if len(a) != len(b) or not sum(a) or not sum(b) or any(abs(x / sum(a) - y / sum(b)) > 1e-9 for x, y in zip(a, b)):
                    issues.append({"reason": "split_ratio_disagrees", "dataset": name, "expected": dataset["splits"],
                                   "observed": ratio[1], "line": num})
    return issues


def tuning_claims(text):
    """Only compare explicit statements about the held-out group's role."""
    out = []
    for i, line in enumerate(plain(text).splitlines(), 1):
        for part in re.split(r"[。；;]|\.\s+(?=[A-Z])", line):
            for match in TEST_FOR_TUNING.finditer(part):
                context = part[max(0, match.start()-18):match.end()]
                # Don't carry a negated statement across "but" into a new clause.
                context = re.split(r",\s*(?:but|whereas)\b|，但", context, flags=re.I)[-1]
                out.append((not bool(re.search(NEGATION, context, re.I)), i, match[0]))
    return out


def verify_methods_and_experiments(chapter_md, repro_md, facts, param_table, *, status_claimed="completed", language="en", rules_path=None):
    """Run conservative mechanical checks. Unverified paraphrases remain visible for review."""
    rules = load_methods_rules(rules_path)
    params = param_table.get("parameters", [])
    datasets, baselines = facts.get("datasets", []), facts.get("baselines", [])
    steps, limits = facts.get("steps", []), facts.get("limitations", [])
    setup, missing = facts.get("setup", {}), facts.get("missing_repro_fields", [])
    conflicts = facts.get("conflicts", [])
    excluded = {str(item.get("field")) for item in facts.get("scope_exclusions", []) if isinstance(item, dict)}
    report_sections = sections(chapter_md)
    prose = "\n".join(body for _, body in report_sections)
    review = []
    issues = {}
    check = "six_element_completeness"
    records = {"steps": row_names(steps), "setup": [setup.get("hardware"), setup.get("software")],
               "parameters": row_names(params), "datasets": row_names(datasets), "baselines": row_names(baselines),
               "limits": [s for s in limits if isinstance(s, str)]}
    coverage = {}
    for key, pattern in TOPICS.items():
        in_section = any((re.search(pattern, title, re.I) or includes(title, records[key])) and text_content(body)
                         for title, body in report_sections)
        in_prose = bool(re.search(pattern, prose, re.I)) or includes(prose, records[key])
        if key == "steps" and not in_prose and not in_section:
            in_prose = procedure_matches(prose, steps)
        coverage[key] = in_section or in_prose
        if not coverage[key] and ("limitations" if key == "limits" else key) not in excluded:
            target = [v for v in records[key] if v]
            if target:
                issues.setdefault(check, []).append({"reason": "topic_has_no_identifiable_content", "topic": key})
            else:
                review.append({"reason": "check_topic_applicability_against_task", "topic": key})
    if not text_content(prose):
        issues.setdefault(check, []).append({"reason": "empty_chapter"})

    p_issues, p_review, p_metrics = inspect_assignments(chapter_md, params, facts)
    review.extend(p_review)
    if p_issues:
        issues["parameter_prose_consistency"] = p_issues
    if conflict_issues := conflict_disclosures(chapter_md, facts, status_claimed, params):
        issues["cross_source_conflict_disclosure"] = conflict_issues

    check = "train_val_test_separation_and_seeds"
    data_errors = inspect_datasets(chapter_md, datasets)
    combined = plain(chapter_md + "\n" + repro_md)
    for key in missing:
        # Stochastic seeds / software / dataset versions can genuinely be inapplicable.
        na = same_line(combined, FIELD_WORDS.get(key, re.escape(key)), r"not applicable|inapplicable|不适用|无需")
        if status_claimed == "completed" and key not in excluded and not na:
            data_errors.append({"reason": "completion_claim_with_unresolved_requirement", "field": key})
    for field, pattern in PHASES.items():
        if field not in excluded and "setup" not in excluded and setup.get(field) and not re.search(pattern, prose, re.I) and not includes(prose, [setup[field]]):
            review.append({"reason": "verify_rephrased_protocol", "field": field})
    source_roles = {eligible for statement in PHASES for eligible, _, _ in tuning_claims(str(setup.get(statement) or ""))}
    if len(source_roles) == 1:
        for eligible, line, quote in tuning_claims(chapter_md):
            if eligible not in source_roles:
                data_errors.append({"reason": "test_set_tuning_polarity_disagrees", "line": line,
                                    "excerpt": quote, "check_source": "setup protocols"})
    if data_errors:
        issues[check] = data_errors

    check = "baseline_fairness_and_repro_checklist"
    if not text_content(repro_md):
        issues[check] = [{"reason": "empty_reproducibility_checklist"}]
    if baselines and "baselines" not in excluded and not coverage["baselines"]:
        issues.setdefault(check, []).append({"reason": "missing_comparison_or_control_description"})
    command = str(setup.get("command") or "")
    if command and " ".join(command.split()) not in " ".join(combined.split()):
        review.append({"reason": "verify_command_and_options_against_original", "expected": command})
    for row in baselines:
        if isinstance(row, dict) and row.get("name") and not includes(prose, [row["name"]]):
            review.append({"reason": "verify_comparator_is_identifiable", "baseline": row["name"]})

    check = "concrete_limitations_and_honesty"
    cliches = [p for p in (rules.get("banned_generic_limitations") or []) if re.search(p, prose, re.I)]
    if cliches:
        issues[check] = [{"reason": "generic_limitation", "patterns": cliches}]
    if not coverage["limits"] and limits and "limitations" not in excluded:
        issues.setdefault(check, []).append({"reason": "missing_scope_or_limitations"})
    if not limits and not re.search(GAP, prose, re.I):
        review.append({"reason": "verify_inferred_limit_is_grounded_in_material_scope"})
    statements = [s for s in limits if isinstance(s, str)] + [setup[k] for k in PHASES if isinstance(setup.get(k), str)]
    if reversals := changed_negation(chapter_md, statements):
        issues.setdefault(check, []).extend(reversals)
    checks = {c: not bool(issues.get(c)) for c in (
        "six_element_completeness", "parameter_prose_consistency", "cross_source_conflict_disclosure",
        "train_val_test_separation_and_seeds", "baseline_fairness_and_repro_checklist", "concrete_limitations_and_honesty")}
    return {
        "schema_version": "writing-09-qa/v1",
        "passed": all(checks.values()),
        "metrics": {**p_metrics, "parameter_count": len(params), "conflict_count": len(conflicts), "step_count": len(steps),
                    "dataset_count": len(datasets), "baseline_count": len(baselines), "missing_repro_count": len(missing)},
        "checks": checks, "coverage": coverage,
        "violations": [{"check": c, **item} for c, items in issues.items() for item in items],
        "review_items": review,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("chapter", "repro", "facts", "params", "out"):
        parser.add_argument("--" + arg, required=True, type=Path)
    parser.add_argument("--status", default="completed")
    parser.add_argument("--language", default="en")
    args = parser.parse_args()
    report = verify_methods_and_experiments(
        args.chapter.read_text(encoding="utf-8-sig"), args.repro.read_text(encoding="utf-8-sig"),
        json.loads(args.facts.read_text(encoding="utf-8-sig")), json.loads(args.params.read_text(encoding="utf-8-sig")),
        status_claimed=args.status, language=args.language)
    write_json_atomic(args.out, report)
    print(json.dumps({"passed": report["passed"], "checks": report["checks"], "violations": len(report["violations"]),
                      "review_items": len(report["review_items"])}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
