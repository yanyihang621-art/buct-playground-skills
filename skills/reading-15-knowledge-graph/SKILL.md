---
name: reading-15-knowledge-graph
description: 构建可查询的多论文文献知识图谱。支持 JATS/JSON 清单、PDF、DOCX 与文本摄取，按论文隔离来源与缩写释义，通过 instance_of 防止跨篇实验实例误合并，绑定逐字短引与位置，执行 8 类可重放查询并导出 GraphML/DOT/Markdown/PNG 预览。触发词：知识图谱、knowledge graph、实体关系抽取、跨篇对齐、图谱查询、增量更新。
---

# 文献知识图谱构建（reading-15-knowledge-graph）

目标：**用 ≤ 18 次工具调用**完成多篇文献精读、实体与有向关系抽取、跨篇保守对齐、可重放查询与契约交付。由你亲自理解原文后编写 `kg_draft.json`，脚本负责版面解析、逐字短引验证、缩写隔离、实例拆分、冲突检测、8 类查询引擎与 GraphML/PNG 导出。

## 五步执行流程

1. **解析语料并生成阅读稿与保底骨架**
   ```bash
   export PYTHONDONTWRITEBYTECODE=1
   S=/skills/weekly-skill
   [ -f "$S/scripts/prepare.py" ] || S=/app/submission
   python3 "$S/scripts/prepare.py" --input /input --case /input/case.json --work /app/work/r15 --out /app/output
   ```
   - 增量任务请追加 `--base-kg <原kg.json路径>`。
   - **必读完整简报**：通读 `/app/work/r15/case_brief.md`（未截断 `task.md`、完整 `output_contract` 树、各篇 DOI/位置类型清单、缩写冲突与共有术语提示）及 `/app/work/r15/answer_template.json`。

2. **按篇精读原文与表格**
   - 直接阅读 `/app/work/r15/reader/*.md`，或使用 `find.py` 定向检索：
     ```bash
     python3 "$S/scripts/find.py" --work /app/work/r15 --abbreviations
     python3 "$S/scripts/find.py" --work /app/work/r15 --boundaries
     python3 "$S/scripts/find.py" --work /app/work/r15 --xml-formulas
     python3 "$S/scripts/find.py" --work /app/work/r15 --paper paper_2 --tables
     python3 "$S/scripts/find.py" --work /app/work/r15 "baseline|accuracy|Table"
     ```
   - 若遇到图片表、扫描页或复杂二维公式，用 `--render-page N --out-png /app/work/r15/pN.png` 查看原页。

3. **编写图谱草稿与案例答案**（写入 `/app/work/r15/kg_draft.json`，长语料可拆分为 `kg_draft_1.json`、`kg_draft_2.json`）
   - 结构参考 `/app/work/r15/kg_draft_template.json` 与 `schemas/kg_ontology.yaml`：
     - `entities`：`label`, `entity_type`, `aliases`, `source_id`, `paragraph_id`, `quote`
     - `relations`：`subject`, `subject_type`, `relation`, `object`, `object_type`, `polarity` (`affirmative|negative`), `conditions`, `evidence_strength`, `source_id`, `paragraph_id`, `quote`
     - `merge_decisions` / `do_not_merge_notes`：记录同概念对齐理由与跨篇不同实验实例/同名缩写禁止合并原因
     - `queries`：结合任务问题写入 ≥ 3 个实际查询（支持 `entity_neighborhood`, `relation_evidence_probe`, `cross_paper_shared_concepts`, `cross_paper_comparison`, `method_metric_comparison`, `paper_evidence`, `path_search`, `conflict_and_boundary_audit`）
   - 若 `output_contract` 要求特定案例问答字段或自定义 JSON 结构，将答案写入 `/app/work/r15/answer.json`（严格对齐 `answer_template.json`）。

4. **确定性验证、查询重放与多格式导出**（最多修正 2 轮）
   ```bash
   python3 "$S/scripts/finalize.py" --case /input/case.json --work /app/work/r15 --out /app/output
   ```
   - 优先清零 `must_fix`（`output_contract` 形状/字段错、`unverified_quote`、`doi_leak`、`fake_pdf_page`、`insufficient_queries`、`invalid_graphml`、`base_node_ids_dropped`），并核查 `warnings`。

5. **最终自查与预算保护**
   - 检查 `/app/output/result.json`、`/app/output/kg.json`、`/app/output/executed_queries.json`、`/app/output/kg.graphml`、`/app/output/kg_report.md` 与 `/app/output/graph_preview.png`。
   - **预算护栏**：若工具调用接近 40 次或耗时超 600 秒，立即基于已核验内容运行 `finalize.py` 交付有据 `partial` 结果。

## 关键科研与溯源原则（红线）

- **抽得准**：只抽取论文实际出现的实体与关系；`quote` 必须逐字复制自 `reader/*.md` 或 `find.py`。
- **连得对**：区分 `direct_author_evidence`、`direct_tabular_evidence`、`prior_work_citation`、`indirect_speculation` 与 `negative_evidence`，保留实验条件、单位、否定与方向。
- **对得齐**：**严禁把第一篇的根 DOI/标题/作者套到第二篇**；按论文解释缩写（如不同论文对 `AP` 的定义不同）；同名通用材料/方法/模型/数据集在不同论文中自动通过 `instance_of` 保留各自实验实例并写入 `do_not_merge_records`，禁止强行合并不同条件或结果。
- **定位真实**：JATS/JSON 段落使用原生 `source_id` + `paragraph_id`，`pdf_page` 必须为 `null`（**绝不编造 `pdf_page=1`**）；同篇多格式文件去重后不计作跨篇证据；未报告或无法判断的问题如实记录在 `uncertainties` 或返回 `no_direct_evidence_unverifiable`。
