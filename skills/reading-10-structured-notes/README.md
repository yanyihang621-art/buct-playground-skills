# reading-10-structured-notes — 文献总结与结构化笔记 Skill

面向 **玻尔产品周赛 · 北化分站赛（第一期）｜阅读10：文献总结与结构化笔记** 的自包含 Agent Skill。围绕 **“对得上、留得住、找得回、用得上”** 四大目标，将单篇或多篇科研文献（PDF / Markdown / TXT）解析为与原文章节对齐、带细粒度页码/区块锚点、区分机器验证与人工复核边界的可检索结构化笔记与 Paper Card。

---

## 1. 目录与模块说明

```text
reading-10-structured-notes/
├── SKILL.md                              # Harbor / Agent 运行主指引
├── GITHUB_URL.txt                        # 公开 GitHub 仓库位置声明
├── README.md                             # 完整使用说明、字段字典、示例与自测记录
├── schemas/
│   ├── field_dictionary.yaml             # 12 项 Paper Card 字段字典、锚点规范与复核分级
│   └── structured_notes.schema.json      # structured_notes.json 输出 Schema
├── templates/
│   └── paper_note_template.md            # 可复用 Obsidian/Markdown 论文笔记模板
├── examples/
│   ├── example_case.json                 # 最小输入样例（标注仅作示例）
│   └── example_result.json               # 最小输出样例（标注仅作示例）
└── scripts/
    ├── __init__.py
    ├── pdf_utils.py / diagnose_pdf.py / extract_formulas.py / extract_tables.py
    ├── ocr_pipeline.py / layout_order.py / structure_parser.py / parse_pdf.py   # 自包含 PDF 解析底座
    ├── ingest_source.py                  # 单/多篇文献及 PDF/MD/TXT 统一摄取
    ├── extract_note_skeleton.py          # 原文章节树对齐笔记 + 12 字段 Paper Card + 前提/局限提取
    ├── extract_knowledge_entries.py      # 术语缩写归并、去重知识点、文内数值冲突检测与倒排索引
    ├── render_notes.py                   # 生成 structured_notes.md / comparison_matrix / notes_index.json
    ├── build_note_result.py              # 对齐 /input/case.json output_contract 的 result.json 构造器
    ├── audit_notes.py                    # 确定性验收审计器（章节对齐、锚点存在性、引文与数值落地核验）
    └── self_test.py                      # 4 套合成测试夹具 + 负向伪造锚点测试 + 基线对照
```

---

## 2. 依赖与运行环境

- **Python**: 3.10+（实测 Python 3.13.9）
- **核心依赖**: `PyMuPDF` (`fitz`)、`pdfplumber`、`pypdf`、`Pillow` (`PIL`)、`numpy`、`PyYAML`
- **可选系统工具**: `tesseract`（用于扫描件/乱码页 OCR；未安装时自动保存 300 DPI 页面 PNG 并诚实标为 `unverifiable`/`partial`）
- **自包含与跨平台**: 所有依赖脚本均直接位于本目录 `scripts/` 内，无符号链接或外部相对路径引用，无硬编码本地绝对路径，兼容 Harbor `/skills/weekly-skill` 容器环境。

---

## 3. 字段字典、锚点格式与复核分级

详细定义见 [`schemas/field_dictionary.yaml`](schemas/field_dictionary.yaml)：

- **统一锚点格式**: `[p.<pdf_page> | <section_id> | <block_id/table_id/eq_id> | bbox:<[x0,y0,x1,y1]>]`，JSON 中同步保留 `printed_page` 与原文 `quote_snippet`。
- **12 项 Paper Card 字段**: `bibliographic`、`research_question`、`data_and_materials`、`methods_and_setup`、`operating_conditions_and_preconditions`、`key_findings_and_metrics`、`tables_and_figures_digest`、`key_equations_and_variables`、`limitations_and_threats_to_validity`、`unreported_or_missing_items`、`reusability_tags`、`verification_summary`。
- **双层复核分级 (`verification_tier`)**:
  - `machine_verified`: 题录信息、章节树、表格/图/公式、缩写频次、带锚点数值发现。
  - `needs_human_review`: 研究问题抽象、数据与方法条件提炼、隐含前提与局限总结、`not_reported` 确认、文内数值冲突核实。

---

## 4. CLI 完整调用步骤

```bash
# 1. 摄取单篇或多篇文献
PYTHONDONTWRITEBYTECODE=1 python3 scripts/ingest_source.py \
  --input /input --case /input/case.json --out /app/work/ingested --ocr auto --crops

# 2. 构建章节笔记、Paper Card、知识点与跨文献对比矩阵
PYTHONDONTWRITEBYTECODE=1 python3 scripts/render_notes.py \
  --manifest /app/work/ingested/ingest_manifest.json --out /app/work/notes

# 3. 构造对齐 /input/case.json 契约的 /app/output/result.json
PYTHONDONTWRITEBYTECODE=1 python3 scripts/build_note_result.py \
  --case /input/case.json \
  --notes /app/work/notes/structured_notes.json \
  --manifest /app/work/ingested/ingest_manifest.json \
  --markdown /app/work/notes/structured_notes.md \
  --comparison-md /app/work/notes/comparison_matrix.md \
  --out /app/output/result.json

# 4. 运行锚点与引文真实性审计
PYTHONDONTWRITEBYTECODE=1 python3 scripts/audit_notes.py \
  --case /input/case.json \
  --result /app/output/result.json \
  --notes /app/work/notes/structured_notes.json \
  --manifest /app/work/ingested/ingest_manifest.json \
  --out /app/work/audit_report.json
```

---

## 5. 本地可复现自测与基线对照

> 以下结果来自运行本目录自带合成测试集（`scripts/self_test.py`）的真实本地观测输出，**仅用于工程回归验证，不代表任何官方赛题成绩**。

复现命令：
```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/self_test.py --out-dir /tmp/reading10_test --compare-baseline
```

本机实测输出（`all_passed: true`）：

| 测试夹具（合成） | 覆盖场景 | 实测结果 |
|---|---|---|
| `s1_catalysis_paper.pdf` | 双栏论文、章节对齐、缩写 `CO2RR` 归并、运行条件 `573 K / 3.0 MPa`、限制条件、摘要 `92.5%` vs Table 1 `91.8%` 数值冲突检测、**伪造锚点负向审计测试** | `pass: true`；`section_coverage_ratio: 1.0`；`anchors_checked/valid: 64/64`（`1.0`）；`numeric_grounding_rate: 1.0`；`conflicts_detected: 1`；`negative_anchor_caught: true` |
| `s2_multi_paper` | PDF + Markdown 双格式混读、跨文献 `comparison_matrix` 与联合检索索引 | `pass: true`；`paper_count: 2`；`comparison_rows: 2`；`anchors_valid: 93/93`（`1.0`）；`numeric_grounding_rate: 1.0` |
| `s3_long_16pages.pdf` | 16 页长文档多章节持续对齐与数值落地校验 | `pass: true`；`sections: 5`；`anchors_valid: 146/146`（`1.0`）；`numeric_grounding_rate: 20/20`（`1.0`） |
| `s4a_encrypted.pdf` / `s4b_scanned.pdf` | 加密拒答与无文字层扫描件降级边界 | `pass: true`；加密返回 `refused`，扫描件返回 `unverifiable` |

与裸单流文本提取（`fitz.get_text("text")` 基线）在同一合成夹具 `S1` 上的对照：
- **章节对齐笔记数**：基线 `0` → Skill `6`
- **通过校验的溯源锚点数**：基线 `0` → Skill `64`
- **术语缩写规范化条目**：基线 `0` → Skill `1`（`CO2RR`）
- **文内数值冲突告警**：基线 `0` → Skill `1`（捕获 `92.5%` vs `91.8%` 差异）

---

## 6. 适用范围与已知限制

1. 确定性脚本基于结构树与中英文科研提示词正则抽取关键句，适合快速构建零幻觉的证据骨架；对于隐含在长段讨论中的深层方法论对比，建议由 Agent 结合 `structured_notes.md` 进一步生成 `--override-json` 并再次经过 `audit_notes.py` 校验。
2. 文内数值冲突检测采用启发式指标名共现规则，可能对“不同样本/不同温度下同一指标的正常差异”触发中等告警，已明确标注 `needs_human_review`。
