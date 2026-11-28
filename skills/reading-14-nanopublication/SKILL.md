---
name: reading-14-nanopublication
description: 科研论文原子断言拆解、最小充分证据绑定、四图 Nanopublication（Head/Assertion/Provenance/PubInfo）生成、RDF（TriG/N-Quads/JSON-LD）序列化与语义核验。触发词：Nanopublication、纳米出版物、原子断言、证据绑定、RDF、TriG、N-Quads、JSON-LD。
---

# Nanopublication 断言拆解与证据绑定（reading-14-nanopublication）

目标：**用 ≤ 18 次工具调用**完成原文精读、原子断言拆解、最小充分证据绑定、四图 RDF 序列化与契约交付。由你亲自精读原文后拆解，脚本负责引文/单位/极性/情态/因果校验、去重、冲突检测与 TriG/N-Quads/JSON-LD 四图导出。

## 五步执行流程

1. **解析材料并读取完整任务简报**
   ```bash
   export PYTHONDONTWRITEBYTECODE=1
   S=/skills/weekly-skill
   [ -f "$S/scripts/prepare.py" ] || S=/app/submission
   python3 "$S/scripts/prepare.py" --input /input --case /input/case.json --work /app/work/r14 --out /app/output
   ```
   - **必读完整文件**：立即通读 `/app/work/r14/case_brief.md`（未截断 `task.md`、`case.json` 全部非材料字段、完整 `output_contract` 树与阅读信号）及 `/app/work/r14/answer_template.json`；若有疑问核对 `/input/case.json` 与 `/input/task.md`。

2. **按大纲精读原文与内联表格**
   - 直接阅读 `/app/work/r14/reader/paper_*.md`（表格已按阅读顺序内联），或用 `find.py` 定位：
     ```bash
     python3 "$S/scripts/find.py" --work /app/work/r14 --section sec_1
     python3 "$S/scripts/find.py" --work /app/work/r14 --tables
     python3 "$S/scripts/find.py" --work /app/work/r14 "may|not|significant|Table"
     python3 "$S/scripts/find.py" --work /app/work/r14 --page 8 --raw
     ```
   - 读全实验条件、表头、单位、对照与作者限定；区分作者直接实验、表格数据、间接推论与综述引用他人工作（`cited_prior_work`）。遇图片表或扫描页可用 `--render-page N` 或查看 `crops/` 核对。

3. **写入原子断言与任务答案**（`/app/work/r14/claims.json` 与 `/app/work/r14/answer.json`）
   - 断言较多时可拆分为 `claims_1.json`、`claims_2.json`（自动合并）；每条 `statement` 仅表达**一条**可独立判真假的原子命题（≤ 280 字符、单句），严禁把整段塞入一条断言。
   - 引文 `quote` **必须逐字复制**自 `reader` 中最小充分片段；表格指标与文本条件分离时，在 `evidence[]` 同时绑定条件段与表行锚点。
   - 严格对齐 `/app/work/r14/answer_template.json` 中的 `fields` 结构与封闭契约限制。

4. **四图序列化、校验与导出**（最多修正 2 轮）
   ```bash
   python3 "$S/scripts/finalize.py" --case /input/case.json --work /app/work/r14 --out /app/output
   ```
   - 清零 `MUST_FIX`（契约错误、`unverified_quote`、`rdf_export_inconsistent`）并处理 `WARNINGS`（`over_broad_assertion`、`possibly_compound`、`hedge_mismatch`、`negation_dropped`、`causal_overclaim`、`unit_mismatch`、`number_not_in_evidence`、`uncontrolled_predicate`）。修正后重跑。

5. **最终自查与预算保护**
   - 检查 `/app/output/result.json`、`/app/output/nanopubs.md`、`/app/output/nanopubs.json` 与 `evidence_check.json`，确认 `TriG == N-Quads == JSON-LD` 四元组数量一致且 `contract_errors=0`。
   - **预算护栏**：工具调用接近 40 次或耗时 > 600s 时，立即运行 `finalize.py` 交付有据 `partial` 结果。

## 22 个受控谓词与拆解规范

`increases`, `decreases`, `improves`, `outperforms`, `comparable_to`, `differs_from`, `causes`（仅限原文明确因果）, `prevents`, `inhibits`, `activates`, `associated_with`, `correlated_with`, `has_value`, `achieves`, `requires`, `enables`, `used_for`, `part_of`, `is_a`, `defined_as`, `produces`, `interacts_with`（完整定义见 `schemas/predicates.yaml` 与 `schemas/extraction_rules.yaml`）。

- **否定与无显著差异**：使用 `predicate: "differs_from"`、`polarity: "negative"`，保留“无显著差异 / no significant difference”。
- **推测与假说**：原句含 `may / might / could / suggest / likely / 可能 / 推测` 时，`modality` 设为 `"hedged"`（或 `"hypothesized"`），绝不设为 `"asserted"`。
- **因果 vs 相关**：原文写 `associated with` / `correlated with` 时使用对应谓词，禁止升级为 `causes`。
- **来源元数据**：严禁编造缺失 DOI/年份/作者，缺项填 `"not_reported"`。

## 输入 JSON 接口速览

- `work/claims.json`（完整结构见 `schemas/claims.schema.json`）：
  `{"paper": {"title": "", "authors": [], "year": "", "doi": "", "version": "", "venue": "", "url": ""}, "claims": [{"id": "c1", "paper_id": "paper_1", "section": "sec_3", "statement": "...", "subject": "...", "predicate": "achieves", "object": "...", "qualifiers": {"condition": "", "system_or_population": "", "value": "", "unit": "", "comparator": ""}, "polarity": "affirmative|negative", "modality": "asserted|hedged|hypothesized", "evidence": [{"anchor": "p3_b04", "quote": "..."}], "evidence_type": "direct_experimental|direct_table_figure|author_statement|indirect_inference|cited_prior_work|hypothesis", "confidence": 0.9, "confidence_basis": "..."}]}`
- `work/answer.json`（对齐 `work/answer_template.json`）：
  `{"fields": {"<严格对应 output_contract>": "..."}, "status": "success|partial|unable", "status_reason": "...", "evidence": {"/summary": [{"anchor": "tab_1", "quote": "..."}]}, "uncertainties": [], "derived_values": []}`

## 异常处理

| 场景 | 处置方式 |
|---|---|
| 加密/损坏 PDF | `prepare.py` 自动写诚实 `unable` 骨架，严禁编造断言 |
| 扫描件无 OCR | 用 `--render-page` 辅助确认；仍不可读则在 `uncertainties` 记 `ocr_unavailable` 并标 `partial`/`unable` |
| 跨断言冲突 | 双方均有原文支撑且极性相反/谓词相反/方向相反时全部保留，由 `finalize.py` 自动记入 `conflicts[]` |
