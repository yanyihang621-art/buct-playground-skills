# 阅读 10｜文献总结与结构化笔记（`reading-10-structured-notes`）

> 将文献理解交还给正在精读原文的 Agent；脚本专注**多格式版面解析、内联表格还原、跨页续接引文校验、数值/单位/否定/推测护栏、契约无损投影与可检索输出**。

## 1. 演进与设计取舍（v1 → v4）

- **v1（`104110`，65.67 分）与 v2（`113257`，57.67 分）**：v2 引入重型规则骨架与多轮 JSON Pointer 审计协议，在 DeepSeek Harness 64 次请求 / 900 秒预算下易耗尽轮次并残留机械模板痕迹（平台评语 *"push your agent for a more complete, genuinely-derived solution"*）。
- **v3（`627c385`）与 v4 核心升级**：
  1. **零截断任务简报**：`prepare.py` 输出完整的 `work/case_brief.md`（无 1,200/1,600/3,000 字符截断，完整保留 `task.md`、`output_contract` 嵌套结构与字段描述、`Reading Signals` 局限/否定/推测线索）与 `work/answer_template.json`。
  2. **多格式全覆盖 + 表格内联**：统一支持 PDF（双栏 XY-cut、跨页严格合并、图/表分离）、DOCX、HTML、LaTeX、JATS XML、CSV/TSV、BibTeX、JSON/JSONL、Markdown/TXT 与内联字段；表格按原阅读顺序内联至 `reader/paper_*.md`。
  3. **确定性事实护栏**：支持跨页/跨栏续接块（`continuation_of`/`continued_by`）精确匹配、数量级等价数值校验（`1M` ↔ `1000000` ↔ `100万`）、物理单位家族一致性校验（`12 Hz` vs `12 kHz` 拦截 `unit_mismatch`）、中英文否定/推测保真。
  4. **无损契约投影**：开放契约下绝不覆盖 Agent 已填字段；封闭契约下支持从 `notes.json` 自动合规投影且输出零多余键；同步生成带 YAML Frontmatter 与真锚点的 `notes.md` 以及倒排索引 `notes_index.json`。

## 2. 快速执行流程

```bash
export PYTHONDONTWRITEBYTECODE=1
S=/skills/weekly-skill
[ -f "$S/scripts/prepare.py" ] || S=/app/submission

# 1) 解析输入、生成 case_brief.md / answer_template.json 与保底 result.json
python3 "$S/scripts/prepare.py" --input /input --case /input/case.json --work /app/work/r10 --out /app/output

# 2) 按大纲检索、查看内联表格或按页检查原 PDF
python3 "$S/scripts/find.py" --work /app/work/r10 --section sec_1
python3 "$S/scripts/find.py" --work /app/work/r10 --tables
python3 "$S/scripts/find.py" --work /app/work/r10 --page 8 --raw

# 3) 写入 work/notes.json（可分片 notes_1.json...）与 work/answer.json 后校验交付
python3 "$S/scripts/finalize.py" --case /input/case.json --work /app/work/r10 --out /app/output
```

## 3. 锚点、字段字典与可复用资源

- **锚点格式**：单篇 `p3_b04`、`tab_1`、`fig_2`、`eq_1`、`docx_para:5`；多篇 `paper_2:p3_b04`。Markdown 显示为 `[p.3 §sec_2 p3_b04]`。
- **可复用资源**：
  - 笔记模板：[templates/paper_note_template.md](templates/paper_note_template.md)
  - 字段字典与 Schema：[schemas/field_dictionary.yaml](schemas/field_dictionary.yaml)、[schemas/notes.schema.json](schemas/notes.schema.json)、[schemas/answer.schema.json](schemas/answer.schema.json)
  - 最小示例（非正式案例）：[examples/example_case.json](examples/example_case.json)、[examples/example_paper.md](examples/example_paper.md)、[examples/example_notes.json](examples/example_notes.json)、[examples/example_answer.json](examples/example_answer.json)
- **校验码说明**（见 `scripts/evidence.py`）：
  - `exact` / `relocated` / `fuzzy`（≥ 0.90）/ `unverified`（`MUST_FIX`）/ `too_short`
  - `unit_mismatch`、`number_not_in_evidence`、`negation_dropped`、`hedge_dropped`、`section_order_mismatch`、`missing_major_sections`

## 4. 适用范围、限制、故障排查与维护

- **依赖要求**：Python ≥ 3.10；`PyMuPDF (fitz)`（推荐）、`pdfplumber`、`pypdf`、`python-docx`、`Pillow`（缺失时自动降级，契约/RDF/HTML/TeX/CSV/JATS 校验均为纯标准库零依赖）。
- **已知限制**：扫描件无 Tesseract OCR 时标记 `ocr_unavailable` 并保存页面截图供视觉排查；复杂二维堆叠公式或纯图曲线需结合 `--render-page` 原页图像核对。
- **故障排查**：
  - `unverified_quote`：用 `find.py --work ... "关键词"` 从 `reader` 逐字复制片段；若跨页断词，保留原跨页全文即可由 `continuation_pairs` 自动匹配。
  - `contract_shape_mismatch`：对照 `work/answer_template.json` 与 `work/case_brief.md` 核对嵌套键名、数据类型与 `additionalProperties: false` 约束。
- **版本与维护**：`v4.0.0` · BUCT Product Arena Team (`reading-10-structured-notes`) · License: MIT / CC-BY-4.0。

## 5. 本地自测与基准

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/self_test.py --out-dir /tmp/reading10-selftest --write-benchmark
```
实测结果记录于 [benchmarks/validator_benchmark.md](benchmarks/validator_benchmark.md)。
