"""Keep measured endpoints attached to the study arm, conditions and original row."""
import hashlib
import json
import re
from decimal import Decimal, localcontext

from parameter_claims import NUMBER, decimal


ARM = ("method", "model", "arm", "group", "treatment", "condition", "方法", "模型", "组别", "实验组")
METRIC = ("metric", "endpoint", "measure", "outcome", "指标", "评价指标", "终点")
VALUE = ("value", "estimate", "mean", "median", "score", "result", "数值", "取值", "均值", "中位数")
UNIT = ("unit", "units", "单位")
CONTEXT = {
    "dataset": ("dataset", "cohort", "population", "数据集", "队列"),
    "version": ("dataset_version", "data_version", "数据版本"),
    "split": ("split", "partition", "数据划分"),
    "stage": ("stage", "phase", "阶段"),
    "protocol": ("protocol", "protocol_id", "规程"),
    "budget": ("budget", "compute_budget", "预算"),
    "aggregation": ("aggregation", "statistic", "summary", "统计量"),
    "seed": ("seed", "random_seed", "随机种子"),
    "fold": ("fold", "fold_id"),
    "repeat": ("replicate", "repeat", "trial", "重复编号"),
    "sample_count": ("n", "sample_size", "样本量"),
    "run_count": ("n_runs", "repeats", "重复次数"),
    "unit_of_analysis": ("unit_of_analysis", "statistical_unit", "分析单位"),
    "batch": ("batch", "batch_id", "批次"),
    "timepoint": ("timepoint", "time_point", "时间点"),
    "site": ("site", "site_id", "地点"),
}
WIDE = re.compile(r"(?:(?:train|val|test|eval|macro|micro|mean|median)_)*(?:accuracy|f1|auc|rmse|mse|mae|r2|precision|recall|specificity|latency|throughput|yield)", re.I)
MARKER = re.compile(r"^\s*\{\{comparison\s+([\w.-]+)\}\}\s*$")


def first(row, keys):
    for key in keys:
        value = row.get(key)
        if isinstance(value, (str, float, int)) and not isinstance(value, bool):
            value = str(value).strip()
            if value:
                return value
    return ""


def row_context(row):
    known = {*METRIC, *VALUE, *UNIT, "metrics", "id", "run", "run_id", "运行编号",
             "sd", "std", "se", "stderr", "ci", "ci_lower", "ci_upper"}
    conditions = {}
    for key, aliases in CONTEXT.items():
        value = first(row, aliases)
        if value:
            conditions[key] = value
            known.update(k for k in aliases if first(row, (k,)) == value)
    arm = first(row, ARM)
    known.update(k for k in ARM if first(row, (k,)) == arm)
    if not first(row, ("value", "estimate", "score", "result", "数值", "取值")):
        for summary in ("mean", "median", "均值", "中位数"):
            if first(row, (summary,)):
                conditions["value_statistic"] = summary
                break
    # Preserve additional scalar conditions (e.g. a specimen temperature).
    # They must match too before a difference is calculated. Nested metadata
    # must be reviewed explicitly instead of disappearing from the comparison.
    for key, item in row.items():
        if key in known or WIDE.fullmatch(key) or key.endswith("_unit"):
            continue
        if item is not None and item != "":
            conditions[key] = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, (dict, list)) else str(item)
    return conditions


def measured_rows(data, source, locator):
    if not isinstance(data, dict):
        return []
    row = {str(k).strip().casefold(): v for k, v in data.items()}
    arm = first(row, ARM)
    if not arm:
        return []
    metric, value = first(row, METRIC), first(row, VALUE)
    endpoints = []
    if metric and value:
        endpoints.append((metric, value, first(row, UNIT), {}))
    else:
        nested = row.get("metrics")
        entries = nested.items() if isinstance(nested, dict) else [(k, v) for k, v in row.items() if WIDE.fullmatch(k)]
        for key, item in entries:
            fallback_unit = first(row, (str(key).casefold() + "_unit", *UNIT))
            if isinstance(item, dict):
                spec = {str(k).casefold(): v for k, v in item.items()}
                endpoints.append((str(key), first(spec, VALUE), first(spec, UNIT) or fallback_unit, row_context(spec)))
            else:
                endpoints.append((str(key), str(item), fallback_unit, {}))
    context = row_context(row)
    records = []
    for metric, value, unit, scoped in endpoints:
        conditions = dict(context)
        for key, detail in scoped.items():
            if key in conditions and conditions[key] != detail:
                conditions["record_" + key] = conditions[key]
            conditions[key] = detail
        if len(value) > 80 or decimal(value) is None or any(len(x) > 180 for x in [arm, metric, unit, *conditions.values()]):
            continue
        identity = json.dumps([source, locator, arm, metric, value, unit, conditions], ensure_ascii=False, sort_keys=True)
        records.append({"id": "R" + hashlib.sha256(identity.encode()).hexdigest()[:12],
                        "arm": arm, "metric": metric, "value": value, "unit": unit, "conditions": conditions,
                        "run_id": first(row, ("run_id", "run", "运行编号")), "source": source, "locator": locator})
    return records


def compare(left, right, operation, chinese):
    if operation not in {"difference", "relative_change"}:
        raise ValueError("choose difference or relative_change")
    if left["arm"].casefold() == right["arm"].casefold():
        raise ValueError("the comparison needs two distinct study arms")
    if (left["source"] == right["source"] and left["locator"].startswith("/") and right["locator"].startswith("/")
            and left["locator"].rsplit("/", 1)[0] != right["locator"].rsplit("/", 1)[0]):
        raise ValueError("the rows belong to separate JSON collections; inspect their enclosing conditions and report separately")
    for key in ("metric", "unit", "conditions"):
        if left[key] != right[key]:
            raise ValueError(f"the rows differ in {key}; report the observations and their conditions separately")
    context = left["conditions"]
    if not any(k in context for k in ("dataset", "split", "stage", "batch", "protocol")):
        raise ValueError("no shared dataset, split, stage, batch or protocol identifies the comparison")
    a, b = decimal(left["value"]), decimal(right["value"])
    if any(n and abs(n.adjusted()) > 200 for n in (a, b)):
        raise ValueError("magnitude outside the comparison arithmetic budget")
    with localcontext() as ctx:
        ctx.prec = 600
        result = a - b
        unit = left["unit"]
        if operation == "relative_change":
            if not b:
                raise ValueError("relative change has a zero reference")
            result = (100 * result / b).quantize(Decimal("0.01"))
            result_unit = "%"
            formula = "100 × (left − reference) / reference"
        else:
            result_unit = ("个百分点" if chinese else "percentage points") if unit in {"%", "percent", "百分比"} else unit
            formula = "left − reference"
    display = format(result, "f")
    def amount(value, units):
        return (value + (" " + units if units else "")).strip()
    metric, larm, rarm = left["metric"], left["arm"], right["arm"]
    lv, rv = amount(left["value"], unit), amount(right["value"], unit)
    diff = amount(display, result_unit)
    context_text = ", ".join(f"{k}={v}" for k, v in context.items())
    if chinese:
        operation_name = "相对变化" if operation == "relative_change" else "差值"
        claim = f"在 {context_text} 条件下，{larm} 的 {metric} 为 {lv}，{rarm} 为 {rv}。以 {rarm} 为参照，{larm} 的{operation_name}为 {diff}。"
    else:
        op = "relative change" if operation == "relative_change" else "difference"
        claim = f"For {context_text}, the reported {metric} was {lv} for {larm} and {rv} for {rarm}. The {op} for {larm} relative to {rarm} was {diff}."
    derivation = {"operation": operation, "formula": formula, "left": left, "reference": right,
                  "value": display, "unit": result_unit}
    return claim, derivation


def expand_comparisons(draft, facts, requested, chinese):
    issues, links, derived, lines = [], [], {}, []
    if not isinstance(requested, dict):
        issues.append({"code": "invalid_comparisons", "detail": "Expected named pairs of measured row IDs."})
        requested = {}
    rows = {row["id"]: row for row in facts.get("run_observations", [])}
    evidence = {(row["source"], row["locator"]): row["text"] for row in facts["evidence"]}
    used = set()
    for line_no, line in enumerate(draft.splitlines(keepends=True), 1):
        match = MARKER.fullmatch(line)
        if not match:
            lines.append(line)
            continue
        name = match[1]
        used.add(name)
        try:
            pair = requested.get(name)
            if not isinstance(pair, dict) or not all(isinstance(pair.get(key), str) for key in ("left", "reference", "operation")):
                raise ValueError("expected left, reference and operation")
            left, right = rows[pair["left"]], rows[pair["reference"]]
            claim, calculation = compare(left, right, pair["operation"], chinese)
            sources = [{"claim": claim, "line": line_no, "source": row["source"], "locator": row["locator"],
                        "quote": evidence[row["source"], row["locator"]]} for row in (left, right)]
            derived[(line_no, claim)] = calculation
            links.extend(sources)
            lines.append(claim + ("\n" if line.endswith("\n") else ""))
        except (KeyError, ValueError, ArithmeticError) as exc:
            issues.append({"code": "unverified_comparison", "name": name, "line": line_no, "detail": str(exc)})
            lines.append(("该对照的条件或数据待核实。" if chinese else "The conditions or values for this comparison require verification.") + "\n")
    for name in requested.keys() - used:
        issues.append({"code": "unused_comparison", "name": name})
    return "".join(lines), links, derived, issues


def swapped_arm_values(claim, records):
    """Report a swap only where the explicit arm assignment equals a different cited arm's value."""
    failures = []
    for row in records:
        peers = [peer for peer in records if peer["arm"] != row["arm"] and
                 all(peer[key] == row[key] for key in ("metric", "unit", "conditions"))]
        if not peers:
            continue
        expected = decimal(row["value"])
        other_values = {decimal(peer["value"]) for peer in peers} - {expected}
        if not other_values:
            continue
        arm = r"(?<![a-zA-Z0-9_])" + re.escape(row["arm"]) + r"(?![a-zA-Z0-9_])"
        metric = re.escape(row["metric"])
        # With several endpoints per cited row, an unlabeled score cannot
        # safely be associated with one metric.
        metric_part = rf"(?:的\s*)?{metric}\s*"
        if len({r["metric"] for r in records}) == 1:
            metric_part = rf"(?:{metric_part})?"
        assigned = arm + rf"\s*{metric_part}(?:scored|achieved|yielded|was|had|=|为|得分为|测得)\s*({NUMBER})(?!\d|\.\d)"
        for match in re.finditer(assigned, claim, re.I):
            if decimal(match[1]) in other_values:
                failures.append({"arm": row["arm"], "metric": row["metric"], "expected": row["value"],
                                 "reported": match[1], "source": row["source"], "locator": row["locator"]})
    return failures
