---
case_id: "<case_id>"
status: "success|partial|unable"
papers: 1
skill: "reading-10-structured-notes"
---

# Literature Notes — `<Paper Title>`

- **Authors**: `<authors or not_reported>`
- **Year / Venue**: `<year> | <venue>`
- **Identifier**: `<DOI or arXiv ID or not_reported>`

## Paper Card

| Slot | Content | Anchors |
|---|---|---|
| research_question | `<核心研究问题与假设>` | `[p.1 p1_b07]` |
| data_materials | `<数据集、催化剂、样本与对照>` | `[p.3 p3_b02]` |
| method | `<方法流程与协议>` | `[p.3 p3_b05]` |
| conditions | `<温度/压力/预算/超参/划分边界>` | `[p.4 p4_b01]` |
| key_findings | `<定量结果（指标、数值、对比基线、条件）>` | `[p.5 tab_1]` |
| conclusions | `<有据结论>` | `[p.8 p8_b10]` |
| limitations | `[author_stated/reader_inferred] <局限与边界>` | `[p.8 p8_b12]` |
| not_reported | `<未报告项>` | `searched: sec_4, tab_2` |

## Section-Aligned Notes

### §sec_1 `<原章节编号与标题>` (`<role>`)
`<按原章节顺序阐明本节目的、做法与发现>`
- **[claim/method/condition/result/limitation]** `<结论与前提条件>` `[p.<page> §<sec_id> <anchor>]` — `"<逐字引文>"`
- *Transition*: `<与下一节的逻辑承接>`

## Knowledge Entries

| Term | Kind | Definition | Anchors |
|---|---|---|---|
| `<术语/缩写>` | `concept\|variable\|method\|dataset\|metric\|result` | `<释义与适用边界>` | `[p.2 p2_b04]` |
