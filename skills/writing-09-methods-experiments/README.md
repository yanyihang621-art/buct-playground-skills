# writing-09-methods-experiments (`写作09｜方法与实验章节写作`)

从实验配置文件（`.yaml`/`.json`）、运行日志（`.log`/`.txt`）、代码脚本（`.py`）与结构化实验说明中自动抽取并对齐参数来源、实验设置、数据集划分与对照条件的可核验方法与实验章节写作 Skill。

## 核心能力与 6 项门控 (`qa_report.json`)

1. `six_element_completeness`：方法步骤、实验设置、参数与来源、数据集与划分、对照实验、可复现性与限制六大要素齐备。
2. `parameter_prose_consistency`：正文参数名称及取值与 `parameter_table.md` / 配置文件 / 实验日志 100% 一致。
3. `cross_source_conflict_disclosure`：跨文件参数冲突必须显式披露并禁止伪报 `completed`。
4. `train_val_test_separation_and_seeds`：训练/验证/测试严格分离，且种子、重复次数与数据集版本缺失必须追问作者。
5. `baseline_fairness_and_repro_checklist`：基线同预算可比与可执行复现清单输出。
6. `concrete_limitations_and_honesty`：禁止空洞限制套话，空输入诚实返回 `refused`。

## 本地自测

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
python skills/writing-09-methods-experiments/scripts/self_test.py --out-dir ..\local_test_outputs\writing-09 --compare-baseline
```
