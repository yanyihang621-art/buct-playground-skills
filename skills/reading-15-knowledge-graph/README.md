# 阅读 15 · 文献知识图谱构建 (`reading-15-knowledge-graph`)

将多篇科研论文（JATS/JSON 清单、PDF、DOCX、Markdown/TXT、HTML、LaTeX）构建为**逐句可溯源、可重放查询、支持增量更新**的多论文知识图谱。

## 核心工作流

完整容器运行命令见 [SKILL.md](SKILL.md)：

1. `scripts/prepare.py`：摄取多格式语料（含双栏 PDF 版面/表格还原、JATS XML 公式与表注提取、同篇多格式去重、缩写冲突扫描），输出 `reader/<source_id>.md`、未截断 `case_brief.md`、`answer_template.json`、`kg_draft_template.json` 及保底骨架 `result.json`。
2. `scripts/find.py`：按全文关键词、论文 `source_id`、章节、表格、缩写冲突（`--abbreviations`）、边界/否定句（`--boundaries`）、JATS XML 公式（`--xml-formulas`）或 PDF 原页截图（`--render-page`）快速精读定位。
3. Agent 撰写 `/app/work/r15/kg_draft.json`（及可选 `answer.json`）。
4. `scripts/finalize.py`：逐字核验每条短引并强制非 PDF `pdf_page=null`、按论文解释缩写、通过 `instance_of` 保持跨篇实验实例隔离、检测矛盾关系、执行 8 类可重放查询、导出 `kg.graphml` / `kg.dot` / `kg_report.md` / `graph_preview.png`，并按 `output_contract` 打包输出 `/app/output/result.json`。

## 目录结构

- `SKILL.md`：Agent 运行指南与溯源红线
- `schemas/kg_ontology.yaml`：实体类型、27 个有向关系及互斥对、5 级证据强度与保守对齐策略
- `schemas/kg_schema.json` & `schemas/query_templates.yaml`：图谱 JSON Schema 与 8 类查询模板
- `templates/kg_draft_template.json`：`kg_draft.json` 最小模板
- `scripts/`：解析内核（`parse_pdf.py`、`extract_tables.py` 等）、`ingest_corpus.py`、`prepare.py`、`find.py`、`kg_engine.py`、`finalize.py`、`self_test.py`
- `examples/`：合成光学示例输入、草稿与示意输出（标明为示例）
- `benchmarks/validator_benchmark.md`：本地多场景自测与真实论文验证报告

## 本地验收命令

```bash
export PYTHONDONTWRITEBYTECODE=1
python3 -B scripts/self_test.py --out-dir ../local_r15_test
```

## 依赖与限制

- 核心图谱、查询、GraphML/DOT/Markdown 导出及契约校验使用 Python 3.10+ 标准库 + `PyYAML`；PDF 解析与 `graph_preview.png` 渲染使用 `PyMuPDF`（`fitz`），扫描页自动回退 OCR（若容器未配置 OCR 引擎则如实降级标注）。
- 默认规则不再硬编码抽取猜测关系，必须由 Agent 精读 `reader/*.md` 后写入 `kg_draft.json` 以保证实体关系与原文语义完全一致。
