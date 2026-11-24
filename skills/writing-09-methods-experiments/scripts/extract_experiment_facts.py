#!/usr/bin/env python3
"""Extract steps, setup, datasets, baselines, parameters, conflicts, and gaps across experimental inputs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from exp_utils import (
    flatten_dict,
    load_methods_rules,
    load_yaml_safe,
    parse_kv_from_log_or_code,
    values_match,
    write_json_atomic,
)

IGNORE_INPUT_FILES = {"case.json", "task.md", "resources.yaml", "challenge.json", "config.json"}
SPEC_NAMES = ("experiment_spec.json", "spec.json")


def _infer_unit_and_target(name: str) -> tuple[str, str]:
    low = name.lower()
    if "lr" in low or "learning_rate" in low:
        return "-", "optimizer"
    if "temp" in low or "temperature" in low:
        return "K", "reactor"
    if "press" in low or "pressure" in low:
        return "MPa", "reactor"
    if "batch" in low:
        return "samples", "dataloader"
    if "epoch" in low:
        return "epochs", "training_loop"
    if "seed" in low:
        return "-", "rng"
    if "ghsv" in low or "flow" in low:
        return "mL/g/h", "feed_flow"
    return "-", "experiment"


def extract_experiment_facts(input_dir: Path, case_data: Dict[str, Any]) -> Dict[str, Any]:
    """Scan configs (.yaml/.json), logs (.log/.txt), code (.py), and spec JSON with cross-source checks."""
    spec: Dict[str, Any] = {}
    config_params: Dict[str, tuple[Any, str]] = {}
    log_params: Dict[str, tuple[Any, str]] = {}
    code_params: Dict[str, tuple[Any, str]] = {}
    scanned_sources: List[str] = []

    spec_hint = case_data.get("spec_file")
    spec_path: Optional[Path] = None
    if spec_hint:
        c = Path(str(spec_hint))
        spec_path = c if c.exists() else (input_dir / c.name)
    elif input_dir.is_dir():
        for nm in SPEC_NAMES:
            if (input_dir / nm).exists():
                spec_path = input_dir / nm
                break

    if spec_path and spec_path.exists():
        spec = json.loads(spec_path.read_text(encoding="utf-8", errors="replace"))
        scanned_sources.append(spec_path.name)

    if input_dir.is_dir():
        for fp in sorted(input_dir.iterdir()):
            if not fp.is_file() or fp.name.lower() in IGNORE_INPUT_FILES:
                continue
            if spec_path and fp.resolve() == spec_path.resolve():
                continue
            suf = fp.suffix.lower()
            if suf in (".yaml", ".yml"):
                scanned_sources.append(fp.name)
                data = load_yaml_safe(fp)
                for k, v in flatten_dict(data).items():
                    short_k = k.split(".")[-1]
                    config_params[short_k] = (v, fp.name)
            elif suf == ".json":
                scanned_sources.append(fp.name)
                data = json.loads(fp.read_text(encoding="utf-8", errors="replace"))
                if isinstance(data, dict):
                    for k, v in flatten_dict(data).items():
                        if not isinstance(v, (list, dict)):
                            config_params[k.split(".")[-1]] = (v, fp.name)
            elif suf in (".log", ".txt"):
                scanned_sources.append(fp.name)
                for k, v in parse_kv_from_log_or_code(fp).items():
                    log_params[k] = (v, fp.name)
            elif suf == ".py":
                scanned_sources.append(fp.name)
                for k, v in parse_kv_from_log_or_code(fp).items():
                    code_params[k] = (v, fp.name)

    # Merge parameters deterministically and detect cross-source conflicts (config vs log vs code)
    all_keys = sorted(set(config_params) | set(log_params) | set(code_params))
    parameters: List[Dict[str, Any]] = []
    conflicts: List[Dict[str, Any]] = []

    # Include explicit structured parameters from spec first
    spec_param_names: set[str] = set()
    for sp in spec.get("parameters", []):
        if isinstance(sp, dict) and sp.get("name"):
            nm = str(sp["name"])
            spec_param_names.add(nm)
            unit_d, tgt_d = _infer_unit_and_target(nm)
            val = sp.get("value")
            src = str(sp.get("source") or (spec_path.name if spec_path else "spec"))
            parameters.append(
                {
                    "name": nm,
                    "value": val,
                    "unit": str(sp.get("unit", unit_d)),
                    "target_component": str(sp.get("target_component", tgt_d)),
                    "source": src,
                }
            )
            # Check conflict if runtime log differs from spec/config value
            if nm in log_params and not values_match(val, log_params[nm][0]):
                conflicts.append(
                    {
                        "code": "cross_source_parameter_conflict",
                        "parameter": nm,
                        "values": {src: val, log_params[nm][1]: log_params[nm][0]},
                        "message": f"Parameter `{nm}` differs between `{src}` ({val}) and `{log_params[nm][1]}` ({log_params[nm][0]}).",
                    }
                )

    for k in all_keys:
        if k in spec_param_names:
            continue
        sources_vals: Dict[str, Any] = {}
        if k in config_params:
            sources_vals[config_params[k][1]] = config_params[k][0]
        if k in log_params:
            sources_vals[log_params[k][1]] = log_params[k][0]
        if k in code_params and k not in config_params:
            sources_vals[code_params[k][1]] = code_params[k][0]

        distinct_vals = list(sources_vals.items())
        if len(distinct_vals) > 1 and not values_match(distinct_vals[0][1], distinct_vals[1][1]):
            conflicts.append(
                {
                    "code": "cross_source_parameter_conflict",
                    "parameter": k,
                    "values": sources_vals,
                    "message": f"Parameter `{k}` has conflicting values across {sources_vals}.",
                }
            )
        # Prefer actual runtime log value if logged, else config, else code
        pref_src, pref_val = (log_params[k][1], log_params[k][0]) if k in log_params else distinct_vals[0]
        unit_d, tgt_d = _infer_unit_and_target(k)
        parameters.append(
            {
                "name": k,
                "value": pref_val,
                "unit": unit_d,
                "target_component": tgt_d,
                "source": pref_src,
            }
        )

    setup = dict(spec.get("setup", {}))
    datasets = list(spec.get("datasets", []))
    steps = list(spec.get("steps", []))
    baselines = list(spec.get("baselines", []))
    limitations = list(spec.get("limitations", []))

    # Identify missing reproducibility items and build concrete author_questions
    rules = load_methods_rules()
    missing_repro: List[str] = []
    author_questions: List[Dict[str, str]] = []

    if not setup.get("software"):
        missing_repro.append("software")
    if not setup.get("hardware"):
        missing_repro.append("hardware")
    if not setup.get("seeds") and "seed" not in {p["name"] for p in parameters}:
        missing_repro.append("seeds")
    if not setup.get("repeats"):
        missing_repro.append("repeats")
    if not setup.get("command"):
        missing_repro.append("command")
    if not datasets or any(not d.get("version") for d in datasets):
        missing_repro.append("dataset_version")

    for mr in missing_repro:
        if mr in rules.get("mandatory_repro_fields", []):
            author_questions.append(
                {
                    "field": mr,
                    "question": f"Please confirm exact `{mr}` (missing from configs/logs/spec; not fabricated in draft).",
                }
            )
    for cf in conflicts:
        author_questions.append(
            {
                "field": cf["parameter"],
                "question": f"Resolve conflict for `{cf['parameter']}`: {cf['values']}. Confirm which value was used for reported results.",
            }
        )

    param_table = {
        "schema_version": "writing-09-parameter-table/v1",
        "parameter_count": len(parameters),
        "parameters": parameters,
        "conflicts": conflicts,
    }
    facts = {
        "schema_version": "writing-09-experiment-facts/v1",
        "scanned_sources": scanned_sources,
        "title": spec.get("title"),
        "steps": steps,
        "setup": setup,
        "datasets": datasets,
        "baselines": baselines,
        "limitations": limitations,
        "missing_repro_fields": missing_repro,
        "conflicts": conflicts,
        "author_questions": author_questions,
    }
    return {"facts": facts, "parameter_table": param_table}


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract verifiable experimental facts and parameter table.")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--case", default=None)
    parser.add_argument("--out-facts", required=True)
    parser.add_argument("--out-params", required=True)
    args = parser.parse_args()

    in_p = Path(args.input_dir)
    case_data = json.loads(Path(args.case).read_text(encoding="utf-8")) if (args.case and Path(args.case).exists()) else {}
    res = extract_experiment_facts(in_p, case_data)
    write_json_atomic(Path(args.out_facts), res["facts"])
    write_json_atomic(Path(args.out_params), res["parameter_table"])
    print(json.dumps({"parameters": res["parameter_table"]["parameter_count"], "conflicts": len(res["facts"]["conflicts"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
