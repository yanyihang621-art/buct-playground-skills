---
name: reading-14-nanopublication
description: 科研论文原子断言拆解、最小充分证据绑定、四图 Nanopublication（Head/Assertion/Provenance/PubInfo）生成、RDF（TriG/N-Quads/JSON-LD）序列化与语义核验。触发词：Nanopublication、纳米出版物、原子断言、证据绑定、RDF、TriG、N-Quads、JSON-LD。
---

# Nanopublication 断言拆解与证据绑定（reading-14-nanopublication）

目标：**用 ≤ 15 次工具调用**完成原文精读、原子断言拆解、最小充分证据绑定、四图 RDF 序列化与核验交付。断言由你亲手阅读后拆解，脚本负责验证锚点、情态/极性、去重、冲突与四图导出。

## 五步快速流程

1. **解析并写骨架**
   ```bash
   export PYTHONDONTWRITEBYTECODE=1
   S=/skills/weekly-skill
   [ -f "$S/scripts/prepare.py" ] || S=/app/submission
   python3 "$S/scripts/prepare.py"
   ```
   本地调试可传入 `--input DIR --case FILE --work DIR --out DIR`（默认 `/input`、`/input/case.json`、`/app/work/r14`、`/app/output`）。此步生成 `reader/paper_*.md`、`index.json`、`meta.json`，并立即写出骨架 `/app/output/result.json`。

2. **按大纲定位并精读**
   ```bash
   python3 "$S/scripts/find.py" --work /app/work/r14 --section sec_1
   python3 "$S/scripts/find.py" --work /app/work/r14 "Table|significant|may|not"
   python3 "$S/scripts/find.py" --work /app/work/r14 --anchor p8_b03 tab_1
   ```
   读全关键段落、表头、单位、操作条件与作者限定；复核引用归属，区分作者实验与综述引用他人工作。

3. **写入原子断言与案例回答**（`/app/work/r14/claims.json` 与 `/app/work/r14/answer.json`）
   - 若断言较多，可拆分 `claims_1.json`、`claims_2.json`（按文件名合并）。
   - 引文 `quote` 必须**逐字复制**自 `reader` 中最小充分片段，不把整段话塞入同一断言。

4. **四图序列化、校验与交付**（最多修正 2 轮）
   ```bash
   python3 "$S/scripts/finalize.py"
   ```
   检查输出的 `MUST_FIX`（契约错误、`unverified` 引文）与 `WARNINGS`（`possibly_compound`、`hedge_mismatch`、`negation_dropped`、`uncontrolled_predicate`、`number_not_in_evidence`）。修正后重跑；若有无法解决的缺失则如实设为 `partial`。

5. **最终自查**
   打开 `/app/output/result.json` 与 `/app/output/nanopubs.md`，核对所有受控谓词、极性/情态、条件限定、来源元数据与契约字段。

## 22 个受控谓词速查

`increases`, `decreases`, `improves`, `outperforms`, `comparable_to`, `differs_from`, `causes`（仅限原文明确因果）, `prevents`, `inhibits`, `activates`, `associated_with`, `correlated_with`, `has_value`, `achieves`, `requires`, `enables`, `used_for`, `part_of`, `is_a`, `defined_as`, `produces`, `interacts_with`。

- **否定与无显著差异**：「无显著差异」写 `predicate: "differs_from"`、`polarity: "negative"`，并在 `statement` 保留“无显著差异 / no significant difference”原措辞。
- **推测与假说**：引文出现 `may / might / could / suggest / likely / 可能 / 推测` 时，`modality` 必须设为 `"hedged"`（或 `"hypothesized"`），绝不升级为 `"asserted"` 或直接因果。
- **复合句拆分**：含多个并列结论、不同实验条件或对比对象的句子，拆成多条独立可判真假的原子断言，各自共享或保留对应 `qualifiers.condition`。
- **来源元数据**：缺省 DOI/年份/作者等严禁编造，缺项保留 `"not_reported"`。

## 输入 JSON 接口格式

### `/app/work/r14/claims.json`
```json
{
  "paper": {"title": "", "authors": [], "year": "", "doi": "", "version": "", "venue": "", "url": ""},
  "claims": [
    {
      "id": "c1",
      "paper_id": "paper_1",
      "section": "sec_3",
      "statement": "单一可判真假的原子陈述句（保留条件、否定与推测强度）",
      "subject": "主体",
      "predicate": "受控谓词",
      "object": "客体",
      "qualifiers": {"condition": "", "system_or_population": "", "value": "", "unit": "", "comparator": ""},
      "polarity": "affirmative|negative",
      "modality": "asserted|hedged|hypothesized",
      "evidence": [{"anchor": "p3_b04", "quote": "最小充分逐字片段"}],
      "evidence_type": "direct_experimental|direct_table_figure|author_statement|indirect_inference|cited_prior_work|hypothesis",
      "confidence": 0.9,
      "confidence_basis": "说明证据直接性、样本/表行与限制"
    }
  ]
}
```

### `/app/work/r14/answer.json`
```json
{
  "fields": {"<严格对应 output_contract 字段名>": "..."},
  "status": "success|partial|unable",
  "status_reason": "完成范围或局限说明",
  "evidence": {"/summary": [{"anchor": "tab_1", "quote": "逐字支撑片段"}]},
  "uncertainties": [{"item": "", "reason": "not_reported|ambiguous|conflict|unreadable", "checked": []}],
  "derived_values": []
}
```

## 异常处理

- **加密 / 损坏 PDF**：`prepare.py` 写诚实骨架，严禁编造断言。
- **扫描件无 OCR**：对不可读页面记 `ocr_unavailable` 并标 `partial` 或 `unable`。
- **跨断言冲突**：两条均有原文支撑但极性相反时全部保留，由 `finalize.py` 自动记入 `conflicts[]`。
