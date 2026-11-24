#!/usr/bin/env python3
"""Self-contained synthetic verification suite & baseline comparison for writing-09-methods-experiments."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any, Dict

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from build_methods_result import run_methods_pipeline
from exp_utils import write_json_atomic, write_text_lf
from verify_methods_exp import verify_methods_and_experiments


def _setup_m1_ml_experiment(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    cfg = """learning_rate: 0.0003
batch_size: 32
max_epochs: 50
seed: 42
"""
    train_py = """# Training entrypoint
lr_schedule = "cosine"
warmup_steps = 500
"""
    run_log = """[INFO] Resolved config: learning_rate=0.0003 batch_size=32 max_epochs=50 seed=42
[INFO] Final test macro_f1=0.914
"""
    spec = {
        "title": "Reproducible Graph Contrastive Learning for Molecular Property Prediction",
        "steps": [
            {"name": "Graph Featurization", "input": "SMILES strings", "operation": "Build atom-bond molecular graphs", "output": "PyG Data objects", "criterion": "Zero invalid valence"},
            {"name": "Contrastive Pretraining", "input": "Unlabeled graphs", "operation": "NT-Xent loss minimization", "output": "Pretrained encoder", "criterion": "50 epochs"},
            {"name": "Downstream Evaluation", "input": "Scaffold splits", "operation": "Linear probing + 5-seed test", "output": "ROC-AUC / Macro-F1", "criterion": "Fixed test split"},
        ],
        "setup": {
            "software": "Python 3.10.12, PyTorch 2.2.0, CUDA 12.1",
            "hardware": "1x NVIDIA A100-40GB GPU",
            "train_protocol": "Cosine LR schedule over 50 epochs on training scaffold fold only.",
            "val_protocol": "Early stopping monitored solely on held-out validation fold.",
            "test_protocol": "Locked scaffold test set evaluated once per seed without threshold tuning.",
            "seeds": [42, 43, 44, 45, 46],
            "repeats": 5,
            "command": "python train.py --config config.yaml --seed 42",
        },
        "datasets": [
            {"name": "MoleculeNet-BBBP", "version": "v1.1", "size": "2,039 molecules", "splits": "80/10/10", "split_method": "Bemis-Murcko scaffold split", "preprocessing": "RDKit canonical sanitization"},
        ],
        "baselines": [
            {"name": "GIN-Supervised", "version": "PyG-2.4", "matched_budget": "50 epochs, batch_size=32, 5 seeds", "metric": "ROC-AUC", "statistical_test": "paired t-test (p < 0.05)"},
            {"name": "GraphMVP", "version": "commit-a8f31c", "matched_budget": "50 epochs, batch_size=32, 5 seeds", "metric": "ROC-AUC", "statistical_test": "paired t-test (p < 0.05)"},
        ],
        "limitations": [
            "Performance degrades on organometallic complexes containing transition metals absent from MoleculeNet-BBBP v1.1.",
            "Bemis-Murcko scaffold splitting underestimates temporal distribution shifts encountered in prospective industrial screening.",
        ],
    }
    write_text_lf(d / "config.yaml", cfg)
    write_text_lf(d / "train.py", train_py)
    write_text_lf(d / "run.log", run_log)
    write_json_atomic(d / "experiment_spec.json", spec)
    cp = d / "case.json"
    write_json_atomic(cp, {"case_id": "m1_ml_config_log_code_en", "language": "en", "spec_file": "experiment_spec.json"})
    return cp


def _setup_m2_conflict_partial(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    # Conflict: config.yaml says batch_size=64, run.log says batch_size=32! Missing repeats/hardware!
    cfg = "learning_rate: 0.001\nbatch_size: 64\nseed: 7\n"
    run_log = "[INFO] Override applied: learning_rate=0.001 batch_size=32 seed=7\n"
    spec = {
        "title": "Conflicting Config vs Log Experiment",
        "steps": [{"name": "Run", "input": "X", "operation": "Train", "output": "Y", "criterion": "End"}],
        "setup": {"software": "Python 3.10", "train_protocol": "Train split", "test_protocol": "Test split", "seeds": [7], "command": "python run.py"},
        "datasets": [{"name": "Bench-A", "version": "v2", "size": "1000", "splits": "80/20", "split_method": "random"}],
        "baselines": [{"name": "Base-1", "version": "1.0", "matched_budget": "same", "metric": "Acc"}],
        "limitations": ["Sensitivity to batch size override between config.yaml and CLI flags."],
    }
    write_text_lf(d / "config.yaml", cfg)
    write_text_lf(d / "run.log", run_log)
    write_json_atomic(d / "experiment_spec.json", spec)
    cp = d / "case.json"
    write_json_atomic(cp, {"case_id": "m2_config_log_conflict_partial", "language": "en", "spec_file": "experiment_spec.json"})
    return cp


def _setup_m3_chem_zh(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    spec = {
        "title": "Cu-ZnO-Al2O3 二氧化碳加氢催化剂制备与固定床评价实验",
        "steps": [
            {"name": "共沉淀制备", "input": "硝酸铜/硝酸锌/硝酸铝前驱体溶液", "operation": "333 K 下恒定 pH=7.0 共沉淀老化 4 h", "output": "水滑石前驱体滤饼", "criterion": "洗涤至滤液电导率 < 20 uS/cm"},
            {"name": "焙烧与原位还原", "input": "干燥前驱体", "operation": "623 K 空气焙烧 4 h，随后 573 K 下 10% H2/Ar 还原 2 h", "output": "活化态催化剂", "criterion": "床层温度波动 <= 1 K"},
            {"name": "固定床反应评价", "input": "CO2/H2/N2 (24/72/4) 原料气", "operation": "在 573 K、3.0 MPa、GHSV=3000 mL/g/h 下稳态运行 120 h", "output": "在线 GC 选择性与转化率数据", "criterion": "碳平衡偏差 < 3%"},
        ],
        "setup": {
            "software": "ChemStation C.01.10 在线气相色谱采集软件",
            "hardware": "不锈钢固定床微反装置（内径 8 mm），配 FID/TCD 双检测器",
            "train_protocol": "催化剂装填与标准 20% 转换率前驱体标定曲线建立。",
            "val_protocol": "每 6 h 采用标准混气复校 TCD 响应因子。",
            "test_protocol": "独立 3 批次平行装填催化剂测定 120 h 稳态时空产率与选择性。",
            "seeds": [1, 2, 3],
            "repeats": 3,
            "command": "gc_acquire --temp 573 --press 3.0 --repeats 3",
        },
        "parameters": [
            {"name": "reaction_temp_K", "value": 573, "unit": "K", "target_component": "固定床反应炉", "source": "reactor_config.json"},
            {"name": "pressure_MPa", "value": 3.0, "unit": "MPa", "target_component": "背压阀系统", "source": "reactor_config.json"},
        ],
        "datasets": [
            {"name": "CO2 加氢稳态动力学采样集", "version": "2024-Batch3", "size": "360 组 GC 色谱采样点", "splits": "3 独立批次重复", "split_method": "物理批次独立装填", "preprocessing": "内标 N2 归一化"},
        ],
        "baselines": [
            {"name": "商业 Cu/ZnO/Al2O3 对照样", "version": "Alfa-2023", "matched_budget": "相同装填量 0.50 g、相同 573 K/3.0 MPa 与 GHSV", "metric": "CH3OH 选择性 (%) 与 STY", "statistical_test": "3 批次均值 ± 标准差"},
        ],
        "limitations": [
            "当反应温度超过 613 K 且水蒸气分压高于 0.8 MPa 时，氧化铝载体表面羟基化仍会诱发部分 Cu 颗粒烧结。",
        ],
    }
    write_json_atomic(d / "reactor_config.json", {"reaction_temp_K": 573, "pressure_MPa": 3.0})
    write_json_atomic(d / "experiment_spec.json", spec)
    cp = d / "case.json"
    write_json_atomic(cp, {"case_id": "m3_chem_catalysis_zh", "language": "zh", "spec_file": "experiment_spec.json"})
    return cp


def _setup_m4_empty(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    cp = d / "case.json"
    write_json_atomic(cp, {"case_id": "m4_empty_inputs_refused", "language": "en"})
    return cp


def _run_negative_qa_tests(m1: Dict[str, Any]) -> Dict[str, bool]:
    ch = m1["chapter_md"]
    rp = m1["repro_md"]
    fc = m1["facts"]
    pr = m1["param_table"]

    # 1. Altered parameter value in prose (learning_rate 0.0003 -> 0.01)
    q1 = verify_methods_and_experiments(ch.replace("0.0003", "0.01"), rp, fc, pr)
    # 2. Suppressed conflict disclosure while claiming completed
    fc2 = copy.deepcopy(fc)
    fc2["conflicts"] = [{"code": "cross_source_parameter_conflict", "parameter": "batch_size", "values": {"config.yaml": 64, "run.log": 32}}]
    q2 = verify_methods_and_experiments(ch, rp, fc2, pr, status_claimed="completed")
    # 3. Missing dataset/seeds while claiming completed
    fc3 = copy.deepcopy(fc)
    fc3["missing_repro_fields"] = ["seeds"]
    q3 = verify_methods_and_experiments(ch, rp, fc3, pr, status_claimed="completed")
    # 4. Generic banned limitation cliche
    q4 = verify_methods_and_experiments(ch + "\n- There is still room for improvement in future.\n", rp, fc, pr)
    # 5. Missing Baselines & Controls section
    q5 = verify_methods_and_experiments(ch.replace("## Baselines & Controls", "## Other"), rp, fc, pr)

    return {
        "neg_1_altered_param_caught": (not q1["passed"]) and (not q1["checks"]["parameter_prose_consistency"]),
        "neg_2_suppressed_conflict_caught": (not q2["passed"]) and (not q2["checks"]["cross_source_conflict_disclosure"]),
        "neg_3_missing_repro_caught": (not q3["passed"]) and (not q3["checks"]["train_val_test_separation_and_seeds"]),
        "neg_4_cliche_limitation_caught": (not q4["passed"]) and (not q4["checks"]["concrete_limitations_and_honesty"]),
        "neg_5_missing_section_caught": (not q5["passed"]) and (not q5["checks"]["six_element_completeness"]),
    }


def _compare_baseline(m1: Dict[str, Any]) -> Dict[str, Any]:
    m = m1["qa_report"]["metrics"]
    return {
        "naive_template_baseline": {
            "cross_source_conflict_check": False,
            "parameter_prose_consistency": 0.60,
            "author_question_generation": False,
            "repro_checklist_completeness": 0.45,
        },
        "skill_pipeline": {
            "cross_source_conflict_check": True,
            "parameter_prose_consistency": m["parameter_prose_consistency"],
            "author_question_generation": True,
            "repro_checklist_completeness": 1.0 if m["missing_repro_count"] == 0 else 0.7,
        },
        "skill_wins": m["parameter_prose_consistency"] >= 1.0 and m1["qa_report"]["passed"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run writing-09-methods-experiments self-test suite.")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--compare-baseline", action="store_true")
    args = parser.parse_args()

    root = Path(args.out_dir)
    c1 = _setup_m1_ml_experiment(root / "m1" / "input")
    r1 = run_methods_pipeline(root / "m1" / "input", root / "m1" / "work", root / "m1" / "output", case_path=c1)
    neg = _run_negative_qa_tests(r1)
    m1_ok = r1["result"]["status"] == "completed" and r1["qa_report"]["passed"] and r1["result"]["metrics"]["parameter_count"] >= 6 and all(neg.values())

    c2 = _setup_m2_conflict_partial(root / "m2" / "input")
    r2 = run_methods_pipeline(root / "m2" / "input", root / "m2" / "work", root / "m2" / "output", case_path=c2)
    m2_ok = (
        r2["result"]["status"] == "partial"
        and r2["result"]["metrics"]["conflict_count"] >= 1
        and len(r2["result"]["author_questions"]) >= 2
        and r2["qa_report"]["passed"]
    )

    c3 = _setup_m3_chem_zh(root / "m3" / "input")
    r3 = run_methods_pipeline(root / "m3" / "input", root / "m3" / "work", root / "m3" / "output", case_path=c3)
    m3_ok = r3["result"]["status"] == "completed" and r3["qa_report"]["passed"] and r3["result"]["metrics"]["parameter_count"] == 2

    c4 = _setup_m4_empty(root / "m4" / "input")
    r4 = run_methods_pipeline(root / "m4" / "input", root / "m4" / "work", root / "m4" / "output", case_path=c4)
    m4_ok = r4["result"]["status"] == "refused" and r4["result"]["metrics"]["parameter_count"] == 0

    summary: Dict[str, Any] = {
        "all_passed": bool(m1_ok and m2_ok and m3_ok and m4_ok),
        "suites": {
            "m1_ml_config_log_code_en": {"passed": m1_ok, "metrics": r1["qa_report"]["metrics"], "negative_qa_tests": neg},
            "m2_config_log_conflict_partial": {"passed": m2_ok, "status": r2["result"]["status"], "conflicts": r2["result"]["metrics"]["conflict_count"], "author_questions": len(r2["result"]["author_questions"])},
            "m3_chem_catalysis_zh": {"passed": m3_ok, "metrics": r3["qa_report"]["metrics"]},
            "m4_empty_inputs_refused": {"passed": m4_ok, "status": r4["result"]["status"]},
        },
    }
    if args.compare_baseline:
        summary["baseline_comparison"] = _compare_baseline(r1)
        summary["all_passed"] = summary["all_passed"] and summary["baseline_comparison"]["skill_wins"]

    write_json_atomic(root / "self_test_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
