---
name: reading-10-structured-notes
description: 科研文献阅读、按原章节结构化笔记、论文卡片、跨文献对比与可核验问答。触发词：文献总结、结构化笔记、论文卡片、structured notes、literature summary、section-aligned reading、evidence grounding。
---

# 文献总结与结构化笔记（reading-10-structured-notes）

目标：**用 ≤ 15 次工具调用**完成精读、答案撰写、逐字引文核验与交付，不让机械模板替代你自己对论文的理解。

## 五步快速流程

1. **解析并生成骨架**（第 1 步即保证可交差）
   ```bash
   export PYTHONDONTWRITEBYTECODE=1
   S=/skills/weekly-skill
   [ -f "$S/scripts/prepare.py" ] || S=/app/submission
   python3 "$S/scripts/prepare.py"
   ```
   本地调试可加 `--input DIR --case FILE --work DIR --out DIR`（默认 `/input`、`/input/case.json`、`/app/work/r10`、`/app/output`）。此步会输出 `case_id`、完整任务文字、`output_contract` 树、每篇论文大纲（`section_id`、页范围、字符数）与 `reader/paper_*.md` 路径，并立即写出骨架 `/app/output/result.json`。

2. **按大纲精读原文**
   - 直接打开 `/app/work/r10/reader/paper_*.md`，或按大纲分段查看：
     ```bash
     python3 "$S/scripts/find.py" --work /app/work/r10 --section sec_3
     python3 "$S/scripts/find.py" --work /app/work/r10 "Table 3|1000000|ablation"
     python3 "$S/scripts/find.py" --work /app/work/r10 --anchor p8_b03 tab_1
     ```
   - 关键表格、图注、脚注与适用条件必须读全表头、单位、分组与预算；对跨栏、公式或图片有疑问时核对原 PDF 对应页。

3. **撰写笔记与答案**（写入 `/app/work/r10/notes.json` 与 `/app/work/r10/answer.json`）
   - 若论文较长，可拆分多个 `notes_1.json`、`notes_2.json`（按文件名合并，防止单次输出超 16K tokens）。
   - 引文 `quote` 必须逐字复制自 `reader`（勿改写），锚点用 `[p3_b04]` 或多篇时 `paper_2:p3_b04`。

4. **核验与导出**（最多修正 2 轮）
   ```bash
   python3 "$S/scripts/finalize.py"
   ```
   查看输出的 `MUST_FIX`（契约错误、`unverified` 引文）与 `WARNINGS`（漏数、丢否定、丢推测、漏节、乱序）。修正后重跑 `finalize.py`；若 2 轮后仍有无法解决的材料缺口，如实将 `status` 设为 `partial` 并写明原因。

5. **最终自查**
   打开 `/app/output/result.json` 与 `/app/output/notes.md`，逐项核对每个子问题、比较组、数值、单位与具体出处是否对齐。

## 写作与证据规则

- **契约第一**：`output_contract` 中的字段与子问题是第一交付物；封闭契约（`additionalProperties: false`）不得多写任何未声明字段。
- **语言与格式**：用案例要求的语言写摘要与答案，引文保留原文逐字片段。
- **保留关键限定**：完整记录条件、比较对象、单位、训练/实验预算、数据集、否定（`not`/`no`/`未`/`无显著差异`）与作者推测语气（`may`/`suggest`/`可能`）。
- **区分来源**：摘要不能替代正文实验；综述中引述他人的结果保留归属（`cited_prior_work`）。
- **区分原值与推导值**：所有差值、比值、变化率在 `answer.json` 的 `derived_values` 中给出算式与输入出处。
- **诚实边界**：未报告项写 `not_reported` 并列出已检查章节；文献内部或跨篇冲突同时保留双方条目；**绝不编造** DOI、年份、缺失数据或不可读扫描页内容。

## 输入 JSON 接口格式

### `/app/work/r10/notes.json`
```json
{
  "language": "zh",
  "papers": [
    {
      "paper_id": "paper_1",
      "bibliography": {"title": "", "authors": [], "year": "", "venue": "", "identifier": ""},
      "sections": [
        {
          "section_id": "sec_1",
          "title": "1 Introduction",
          "role": "motivation|background|method|experiment|result|discussion|conclusion|appendix",
          "summary": "按原章节顺序阐明本节问题、做法与发现",
          "points": [
            {
              "text": "具体论断或数值结论（保留条件与对比对象）",
              "type": "claim|method|condition|result|limitation|definition",
              "evidence": [{"anchor": "p1_b09", "quote": "逐字复制自 reader 的原句"}]
            }
          ],
          "links_to_next": "与下一节的逻辑承接"
        }
      ],
      "card": {
        "research_question": {"text": "", "evidence": [{"anchor": "p1_b07", "quote": ""}]},
        "data_materials": {"text": "", "evidence": []},
        "method": {"text": "", "evidence": []},
        "conditions": [{"text": "", "evidence": []}],
        "key_findings": [
          {"text": "", "metric": "", "value": "", "comparator": "", "condition": "", "evidence": []}
        ],
        "conclusions": {"text": "", "evidence": []},
        "limitations": [{"text": "", "source": "author_stated|reader_inferred", "evidence": []}],
        "not_reported": [{"item": "未报告项", "searched": ["sec_4", "tab_2"]}]
      },
      "knowledge": [
        {"term": "", "kind": "concept|variable|method|dataset|metric|result", "definition": "", "aliases": [], "evidence": []}
      ]
    }
  ],
  "comparison": [{"dimension": "对比维度", "values": {"paper_1": ""}}]
}
```

### `/app/work/r10/answer.json`
```json
{
  "fields": {"<严格对应 output_contract 声明的字段名>": "..."},
  "status": "success|partial|unable",
  "status_reason": "完成范围或局限说明",
  "evidence": {
    "/answer_field": [{"anchor": "tab_1", "quote": "逐字支撑片段"}]
  },
  "uncertainties": [
    {"item": "缺失或无法判定的内容", "reason": "not_reported|ambiguous|conflict|unreadable", "checked": ["sec_4", "tab_2"]}
  ],
  "derived_values": [
    {"field": "/delta", "value": "-0.021", "formula": "0.277 - 0.298", "inputs": [{"anchor": "tab_1", "quote": "..."}]}
  ]
}
```

## 异常处理

- **加密或损坏 PDF**：`prepare.py` 自动写诚实骨架（`unable`/`partial`），切勿猜测正文内容；直接说明不可读原因。
- **扫描版且无 OCR**：在 `uncertainties` 标明页码范围与 `ocr_unavailable`，仅基于可读页面作答。
- **预算紧张**：先保证 `answer.json` 的核心契约字段与关键节笔记，运行 `finalize.py` 交付有据部分结果。
