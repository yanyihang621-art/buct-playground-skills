---
name: reading-10-structured-notes
description: 科研文献阅读、按原章节结构化笔记、论文卡片、跨文献对比与可核验问答。触发词：文献总结、结构化笔记、论文卡片、structured notes、literature summary、section-aligned reading、evidence grounding。
---

# 文献总结与结构化笔记（reading-10-structured-notes）

目标：**用 ≤ 18 次工具调用**完成精读、结构化笔记与契约交付，用确定性脚本做版面解析与引文/数值/单位/否定校验，由你亲自理解并撰写笔记与答案（严禁模板拼接）。

## 五步执行流程

1. **解析材料并读取完整任务简报**（第 1 步即写保底 `result.json`）
   ```bash
   export PYTHONDONTWRITEBYTECODE=1
   S=/skills/weekly-skill
   [ -f "$S/scripts/prepare.py" ] || S=/app/submission
   python3 "$S/scripts/prepare.py" --input /input --case /input/case.json --work /app/work/r10 --out /app/output
   ```
   - **必读完整文件**：立即通读 `/app/work/r10/case_brief.md`（含未截断 `task.md`、`case.json` 全部非材料字段、完整 `output_contract` 树与阅读信号）以及 `/app/work/r10/answer_template.json`；若有疑问直接核对 `/input/case.json` 与 `/input/task.md`。

2. **按原大纲精读原文与内联表格**
   - 直接阅读 `/app/work/r10/reader/paper_*.md`（表格已按原阅读顺序内联到对应章节），或使用 `find.py` 定向检索：
     ```bash
     python3 "$S/scripts/find.py" --work /app/work/r10 --section sec_3
     python3 "$S/scripts/find.py" --work /app/work/r10 --tables
     python3 "$S/scripts/find.py" --work /app/work/r10 "Table 3|limitation|significant"
     python3 "$S/scripts/find.py" --work /app/work/r10 --page 8 --raw
     ```
   - 核对 `case_brief.md` 列出的 `Reading Signals`（局限/否定/推测线索），读全关键表格的表头、单位、分组、预算与脚注；若遇到图片表、扫描页或复杂二维公式，用 `--render-page N --out-png /app/work/r10/pN.png`（或查看 `ingested/papers/paper_*/crops/`）对照原页图片，不能读清时诚实标注。

3. **撰写结构化笔记与任务答案**（写入 `/app/work/r10/notes.json` 与 `/app/work/r10/answer.json`）
   - 若论文较长，可拆分 `notes_1.json`、`notes_2.json`（按文件名自动合并，防单次输出超 16K tokens）。
   - 引文 `quote` **必须逐字复制**自 `reader` 或 `find.py`（勿改写/省略中间词），锚点写 `p3_b04`、`tab_1`（多篇写 `paper_2:p3_b04`）。
   - 严格对齐 `/app/work/r10/answer_template.json` 中 `fields` 的字段名、嵌套结构、类型与枚举；封闭契约（`additionalProperties: false`）不得多写未声明字段。

4. **核验与导出**（最多修正 2 轮）
   ```bash
   python3 "$S/scripts/finalize.py" --case /input/case.json --work /app/work/r10 --out /app/output
   ```
   - 优先清零 `MUST_FIX`（契约字段/类型错、`unverified_quote`）并核查 `WARNINGS`（`unit_mismatch`、`number_not_in_evidence`、`negation_dropped`、`hedge_dropped`、`missing_major_sections`、`section_order_mismatch`）。
   - 修正后重跑 `finalize.py`；若因扫描件/加密/材料缺失仍有未决项，如实设为 `partial` 或 `unable` 并写清 `uncertainties`。

5. **最终自查与预算保护**
   - 检查 `/app/output/result.json`、`/app/output/notes.md`、`/app/output/notes_index.json` 与 `/app/output/evidence_check.json`，确认子问题、数值、单位、比较对象与出处一一对齐。若任务额外要求可编辑 `.docx`/图表的视觉预览，按要求生成真实 PDF/PNG 预览并检查。
   - **预算护栏**：若工具调用接近 40 次或耗时超过 600 秒，立即基于已核验内容运行 `finalize.py` 交付有据 `partial` 结果，勿拖到超时。

## 写作与证据原则

- **对得上**：`sections` 严格遵循原论文层级与出现顺序，记录 `links_to_next`，禁止套用通用模板重排结构。
- **留得住**：完整保留实验条件、对照基线、单位、预算、数据集、否定（`not`/`no`/`未`/`无显著差异`）与推测情态（`may`/`suggest`/`可能`）；文献自身实验与综述引述他人工作（`cited_prior_work`）严格区分。
- **找得回**：Paper Card、章节要点、知识条目与 `answer.json` 均附锚点与逐字短引；推导差值/比值写入 `derived_values` 并注明算式与原值锚点。
- **诚实边界**：未报告写 `not_reported` 并列出已检索章节；文献内部或跨篇数值冲突同时保留双方条目；绝不编造缺失数据或不可读页内容。

## 输入 JSON 接口速览

- `work/notes.json`（完整字典见 `schemas/field_dictionary.yaml` 与 `schemas/notes.schema.json`）：
  `{"language": "zh", "papers": [{"paper_id": "paper_1", "bibliography": {...}, "sections": [{"section_id": "sec_1", "title": "...", "role": "motivation|background|method|experiment|result|discussion|conclusion|appendix", "summary": "...", "points": [{"text": "...", "type": "claim|method|condition|result|limitation|definition", "evidence": [{"anchor": "p1_b09", "quote": "..."}]}], "links_to_next": "..."}], "card": {"research_question": {"text": "", "evidence": []}, "data_materials": {"text": "", "evidence": []}, "method": {"text": "", "evidence": []}, "conditions": [{"text": "", "evidence": []}], "key_findings": [{"text": "", "metric": "", "value": "", "comparator": "", "condition": "", "evidence": []}], "conclusions": {"text": "", "evidence": []}, "limitations": [{"text": "", "source": "author_stated|reader_inferred", "evidence": []}], "not_reported": [{"item": "", "searched": ["sec_4"]}]}, "knowledge": [{"term": "", "kind": "concept|variable|method|dataset|metric|result", "definition": "", "aliases": [], "evidence": []}]}], "comparison": []}`
- `work/answer.json`（完整结构见 `work/answer_template.json` 与 `schemas/answer.schema.json`）：
  `{"fields": {"<严格对应 output_contract>": "..."}, "status": "success|partial|unable", "status_reason": "...", "evidence": {"/field": [{"anchor": "tab_1", "quote": "..."}]}, "uncertainties": [{"item": "...", "reason": "not_reported|ambiguous|conflict|unreadable", "checked": []}], "derived_values": [{"field": "/delta", "value": "-0.021", "formula": "0.277 - 0.298", "inputs": []}]}`

## 异常处理

| 场景 | 处置方式 |
|---|---|
| 加密/损坏 PDF | `prepare.py` 自动写诚实 `unable` 骨架，直接报告原因，绝不猜测内容 |
| 扫描件无 OCR / 复杂 2D 公式 | 用 `--render-page` 或 `crops/` 辅助核对；仍不可读则在 `uncertainties` 记 `ocr_unavailable` 并标 `partial`/`unable` |
| 多篇/混合格式（DOCX/JATS/HTML/TeX/CSV） | `prepare.py` 统一生成 `paper_1..N` 与 `comparison.md`，锚点使用 `paper_k:<anchor>` |
