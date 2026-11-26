---
name: reading-10-structured-notes
description: Read scientific papers and their tables, write section-aligned notes, answer case-specific questions with verifiable quotations, and package searchable paper cards and comparison notes. Supports PDF, Markdown, text, JSON articles and JATS.
---

# 文献总结与结构化笔记

你是本次案例的科研读者。交付物是对本次问题的回答、可继续使用的论文笔记和支持它们的原文位置。按案例要求选择语言、字段、文件名和证据范围。下面两次运行之间，由你读原文、回答问题并写出解读。

## 一、建立可复核的阅读材料

先读 `/input/task.md`、`/input/case.json` 的完整要求和 `output_contract`。查看材料清单、补充文件、同一篇论文的多个版本和优先采用的原始来源。特别记下比较对象、评价指标、条件、要求回答的子问题以及限定的输出格式。

```bash
export PYTHONDONTWRITEBYTECODE=1
S=/skills/weekly-skill
[ -f "$S/scripts/run_case.py" ] || S=/app/submission
python3 "$S/scripts/run_case.py" prepare
```

普通本地使用可将 `S` 设为本目录。两个命令都支持 `--input DIR --case FILE --work DIR --out DIR`。默认读取 `/input`，在 `/app/work/reading` 工作，交付至 `/app/output`。prepare 递归读取可支持的材料，写出原始文档、逐篇便于检索的完整段落文本和表格，以及清楚标记的阶段性结果。重复解析前，请查看 `ingested/ingest_manifest.json` 并核对原始文件，显式指定而未找到的来源会列为不确定项。未支持的材料可由 Agent 直接打开，保留原始位置。

## 二、精读和写作

打开 `/app/work/reading/reader/paper_*.md`，用论文目录定位相关章节。每段前给出 paper_id、section_id、element_id 及物理页、源文件行或原生路径。表格、图注、公式在同一文件内也有独立条目。必要时打开 PDF 对照文字、图形、坐标轴和跨页列，尤其是显著数值、脚注和条件。JSON Pointer 或 XML 路径可以直接定位结构化原文。章节解析本身有疑问时用原文实际标题和页码澄清。案例的辅助清单、题目文字、示例和领域标签需与论文内容区分。

逐项写出读者可以独立理解的答案。

- 按原有章节、论证顺序和层级说明该节的问题、做法、发现及其与下一节的关系。方法与操作条件应分开记录。摘要不能代替正文实验。文献综述中引述的他人成果保留归属。
- 数值比较读全表头、单位、分组和脚注。说明比较组与对照组、指标方向和可比较的实验预算。保留不利、无显著差异和作者的推测；跨模型、跨数据集或不同预算的结果单列。对提问涉及的每个数值绑定实际行或段落。
- 修订论文卡片中的研究问题、数据与材料、方法、适用条件、主要发现及限制。初稿依靠规则选择候选证据，须将直接摘录与自己的解释分开。摘要、比较矩阵及卡片共享复核后的内容。
- 每项结论给可重新找到的确切出处。对缺失、无法辨认和来源冲突分别说明。多篇来源不得混用局部 ID，报告它们相同与不同的实验条件。
- 按案例契约把问题的实质答案写进 `fields`。特别留意嵌套对象、数组、语言和额外字段限制。通用笔记和原文索引保存在 `notes/`，契约中的答案字段需能独立使用。如果要求自定义成品路径，在 `/app/output` 写出对应文件并检查内容。

将复核结果存入 `/app/work/reading/review.json`。以实际文件 ID 替换以下接口示意。仅填写此次任务的字段。

```json
{
  "case_id": "example",
  "papers": {
    "paper_1": {
      "sections": {
        "sec_1": {
          "summary": "Interpret the section in the case language.",
          "evidence": [{"block_id": "p1_b02", "quote_snippet": "Copy a supporting clause verbatim."}]
        }
      },
      "card": {
        "key_findings_and_metrics": {
          "summary": "Write the result with its comparator and conditions.",
          "evidence": [{"table_id": "tab_1", "quote_snippet": "Copy the relevant row."}]
        }
      }
    }
  },
  "fields": {"answer": {"conclusion": "Write the answer to the actual question."}},
  "field_evidence": {
    "/answer/conclusion": [
      {"paper_id": "paper_1", "element_id": "tab_1", "quote_snippet": "Copy the pertinent data row."}
    ]
  }
}
```

`field_evidence` 的键是进入 `fields` 的 JSON Pointer，如 `/answer/groups/0/metric`。用同一条原文可以定位多个具体字段。同一比较可引用多个表头、行、脚注和段落。允许 `block_id`、`table_id`、`figure_id`、`eq_id` 或统一 `element_id`。没有额外题目字段时可以只交逐章和卡片复核。确实未报告的卡片字段可写 `{"status":"not_reported","summary":"说明所查范围及未报告的内容","searched_sections":["实际节 ID"]}`。字段缺证、资料损坏及 OCR 遗漏需在案例结果说明，当可用材料仍能回答部分问题时保留已完成部分。

## 三、生成最终结果并验收

每个自定义科学答案的具体值须由 `field_evidence` 中对应指针或上级指针绑定原文。题目要求但证据缺失时，按契约填写可表达缺失的值，同时在 `field_limits` 添加同一指针及 `{"reason":"实际缺失的证据或原因","checked":["检查过的文件、章节或表格"]}`。运行会保留已有答案，记录未完成字段和可接手位置。计算得到的新数值应注明算式、输入值及其出处，区分原文值与推导值。

对差值、和、比值与百分变化，可在 `derivations` 以答案值的指针指定算式。如 `{"operation":"difference","operands":[2.4,1.9],"decimal_places":1,"unit":"nm"}`。两项输入都须出现在该指针绑定的原句中。操作选项为 `difference`、`sum`、`ratio`、`percent_change`，最后一种以第二个值为基准。若题意涉及更复杂的计算，单独交付可重跑的计算及输入来源，按契约注明推导。

论文级解析状态与问题完成状态可分开记录。若原文中某些公式或图片无法识别，但所有被问到的事实均已由可读正文或表格逐项确认，可在 `fields.status` 填写契约要求的完成状态，在 `status_reason` 写明已覆盖的范围和不影响该结论的材料告警。仍影响答案的缺口应如实标为部分完成。

```bash
python3 "$S/scripts/run_case.py" finish
```

`finish` 复用已解析材料，核对输入摘要、引文和来源，重新渲染复核后的笔记，并检查最终契约（含嵌套类型、必填字段与闭合对象）。错误细节在 `/app/work/reading/latest_audit.json`；修复复核稿再运行。程序完成后重新打开 `/app/output/result.json`、`notes/structured_notes.md`、`notes/comparison_matrix.md`、`notes/case_evidence.json` 以及题目明确要求的成品。检查每个子问题、所有对照组、真实数值与具体定位。审计可以核对引文、数字和部分语义矛盾，跨语种推断和科学解释由你逐项对照原文。预算不足时将有依据的进度和仍缺的证据作为部分结果交付。

字段定义及完整示例见 README 和 schemas。底层摄取、渲染、结果打包与审计仍可分别调用，可用 `python3 scripts/self_test.py --out-dir /tmp/reading10-tests` 验证。
