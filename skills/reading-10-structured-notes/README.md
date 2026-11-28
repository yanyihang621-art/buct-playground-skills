# 阅读 10｜文献总结与结构化笔记（`reading-10-structured-notes`）

> 将繁重的论文理解交还给正在阅读原文的 Agent，脚本只负责**版面还原、锚点索引、逐字引文校验、事实不变量告警与多格式交付**。

## 1. 为什么重构为 v3（v1 → v3 设计取舍）

- **v1（`104110`，官方终分 65.67）** 与 **v2（`113257`，官方终分 57.67）**：v2 引入了重型规则骨架生成与过长多轮审计协议（`review.json` 含复杂 JSON Pointer 审核、高达 670 KB 的机械产物），导致 DeepSeek Harness 在 64 次请求 / 900 秒预算内易消耗轮次并被判定为机械模板而非“genuinely-derived”结果，总分反而下降 8 分。
- **v3 核心思路（做减法）**：
  1. `prepare.py` 一次完成 PDF/Markdown/JATS/JSON 递归解析，输出紧凑分节阅读稿 `reader/paper_*.md`、检索索引 `index.json`，并立即写出符合 `output_contract` 的骨架 `result.json`（先保底）。
  2. Agent 借助 `find.py` 按大纲精读原文，亲手撰写结构化笔记 `work/notes.json`（支持分片）与问题答案 `work/answer.json`。
  3. `finalize.py` 执行确定性校验（逐字引文匹配、错位锚点自动重定位、数字一致性、否定与推测强度保留、章节顺序与遗漏检查、契约闭环校验），生成 `result.json`、`notes.md`、`notes.json`、`evidence_check.json`（多篇另出 `comparison.md`）。

## 2. 快速执行流程

```bash
export PYTHONDONTWRITEBYTECODE=1
S=/skills/weekly-skill
[ -f "$S/scripts/prepare.py" ] || S=/app/submission

# 1) 解析材料并写骨架 result.json
python3 "$S/scripts/prepare.py" --input /input --case /input/case.json --work /app/work/r10 --out /app/output

# 2) 按大纲检索或分节精读
python3 "$S/scripts/find.py" --work /app/work/r10 --section sec_1
python3 "$S/scripts/find.py" --work /app/work/r10 "Table 3|ablation"

# 3) Agent 写入 /app/work/r10/notes.json 与 /app/work/r10/answer.json 后校验交付
python3 "$S/scripts/finalize.py" --case /input/case.json --work /app/work/r10 --out /app/output
```

## 3. 锚点与字段字典

- **锚点格式**：单篇使用 `p3_b04`、`tab_1`、`fig_2`、`eq_1`；多篇使用 `paper_2:p3_b04`。Markdown 中渲染为 `[p.3 §sec_2 p3_b04]`。
- **完整字段与 Schema**：见 [schemas/field_dictionary.yaml](schemas/field_dictionary.yaml)、[schemas/notes.schema.json](schemas/notes.schema.json)、[schemas/answer.schema.json](schemas/answer.schema.json)。
- **核心核验规则**（实现于 `scripts/evidence.py`）：
  - `exact` / `relocated` / `fuzzy`（≥ 0.90）/ `unverified` / `too_short`；错位锚点自动重定位并记录在 `change_records`。
  - `number_not_in_evidence`：笔记或答案中的数值必须在引文、锚点原文或 `derived_values` 中有据可查。
  - `negation_dropped` / `hedge_dropped`：跨语种识别否定词与推测情态词，防止把“未显著改善”或“may induce”改写为绝对肯定结论。

## 4. 适用范围、限制与依赖

- **支持格式**：数字版 PDF（含双栏重排与表格提取）、Markdown、TXT、JATS XML、结构化 JSON 及内联文本；支持加密/损坏/扫描件降级。
- **已知限制**：扫描件无 Tesseract OCR 时标记 `ocr_unavailable` 并诚实降级；复杂二维堆叠公式或纯图表格需结合原 PDF 页面核对。
- **依赖**：Python ≥ 3.10；`PyMuPDF (fitz)` / `pdfplumber` / `pypdf`（可选降级，契约校验零外部依赖纯标准库实现）。

## 5. 本地自测与示例（非正式案例）

- 最小示例位于 `examples/`（均标注“示例，非正式案例”）。
- 运行自测与故障注入基准：
  ```bash
  PYTHONDONTWRITEBYTECODE=1 python3 scripts/self_test.py --out-dir /tmp/reading10-selftest --write-benchmark
  ```
- 实测故障注入报告见 [benchmarks/validator_benchmark.md](benchmarks/validator_benchmark.md)。
