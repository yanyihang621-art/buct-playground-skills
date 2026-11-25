---
name: writing-04-references
description: "Edit citations in an academic manuscript, reconcile multi-format reference libraries, renumber after edits, verify sentence-level evidence, and deliver the editable draft and a reproducible reference audit. Use for citation placement, deduplication, source verification and style conversion."
---

# 参考文献与引用管理

你是本案例的引文编辑。阅读任务约束、稿件、文献库和可用原文。交付一份作者能继续编辑的稿件、一份与正文同步的文献表，以及能从每处改动追溯到材料的证据。使用案例指定的语言、期刊体例和文件名。以下流程由运行当前案例的 Agent 执行。

## 1. 建立初稿与证据索引

读 `/input/case.json`、任务说明和所有材料路径。记下受众、引文样式、指定修改、输出名称及 `output_contract` 字段和类型。对比正文、脚注、图表注、附录和旧文末表，确定原编号的依据。先跑初稿，在 `/app/work` 的任意工作目录均可调用：

```sh
SKILL_DIR=/skills/weekly-skill
[ -f "$SKILL_DIR/scripts/build_reference_result.py" ] || SKILL_DIR=/app/submission
python3 "$SKILL_DIR/scripts/build_reference_result.py" --input-dir /input --case /input/case.json --work-dir /app/work --output-dir /app/output
```

读 `/app/output/result.json`、`/app/output/evidence/{material_inventory,canonical_library,dedup_report,citation_map,qa_report}.json` 及 `/app/work/references/original_manuscript.txt`。支持性文件的页码、段落、正文在 `/app/work/references/evidence_passages.json`，疑处打开原件。文献库没有原文时，优先使用实际提供的摘要。两份稿件角色不清时依据任务说明在 review 中指定 `manuscript_file` 与 `bib_files` 后重跑。对损坏或漏读的文件，可用环境中可用的 Office/PDF/OCR 工具读取，并在交付件内记录真实页码和处理方式。保持输入原件可追溯。

## 2. 逐条编辑和判断

- 在每段找出需引证的数字、机制、比较、结论与方法归属。对引用位置错误、段末混引、连续几句共用一引或遗漏的论断，逐句查到对应文献。查明材料支持的群体、条件、指标、方向和强度。需要插引时用唯一短语紧贴相应论断。来源不足时保留待确认项，说明具体缺哪份证据。
- 从显式编号、正文旧书目条目、DOI 或题名和作者组合定位旧序号。引文库导入顺序不能证明旧号。逐条核实同一引文键的不同来源、双语题名、预印本与正式版。保留不同 DOI、勘误、会议扩展版等差异及依据。按首次引用排序，核对多引、图表注、附录和文末条目。核对易混淆的同作者同年份文献及 APA 后缀。
- 逐项读 `qa_report.claim_evidence`。词语重合只能帮助找资料。对每条「论断—来源」写结论和理由。用 `supported` 表示证据确实覆盖该句陈述，用 `partial` 标出适用边界，用 `mismatch` 指明矛盾，用 `insufficient` 列明材料缺口。仅有题名时通常只能证明主题，具体数据或因果结论需要更直接的材料。
- 修复错引时记录原句、改句或改号、来源和原因。未提供合适文献时给出作者可操作的问题。如题面要求在线 DOI 核验且允许联网，使用 `--verify-doi-online`，并读结果内的元数据比对和未查询项。无网时保留本地来源级别。

把编辑决定写入 `/app/work/reference_review.json`。可先提交插引和改句、重跑拿到新的行号，再填逐条语义审阅。review 的可选字段如下：

| 字段 | 内容 |
| --- | --- |
| `style`, `manuscript_file`, `bib_files` | 依据任务选择目标样式、相对输入路径或文件列表 |
| `original_number_map` | 旧号到确定的 cite_key 的对象。用原表、记录标签或材料中的映射核实 |
| `insert_claims` | 数组。每项给出实际稿件中唯一的 `target_phrase`、`cite_keys`，需要时可加 `line`。用 `replace_marker: true` 精确替换旧引文标记 |
| `text_edits` | 对内容或引用位置需调整时，给出唯一的 `before`、`after`、`reason` 和 `source`。用现有术语，保存原有数字、方向与限定条件 |
| `non_citations` | 数学区间等形似引文时，给出 `line`、`marker`、原句连续子串 `context` 和 `reason` |
| `semantic_reviews` | 每个来源各一行，含 `line`、`occurrence_index`（从 1 起）、`cite_key`、`claim_quote`、`verdict` 和 `reason`。有直接证据时填 `source_field` 与逐字 `source_quote` |
| `source_field` | `abstract` 或 `title` 对应规范库字段。用全文时填 `evidence_file`，另给输入根下的相对 `source_file`，PDF 还需给从 1 起的 `page`。核对文献身份及原句 |
| `contract_values` | 根据案例专属字段的定义及类型写真实内容。输出文件路径须指向已经生成的产物 |
| `docx`, `uncertainties` | 案例指定 Word 时用 `true`；需人工确认的条目给出位置、问题和需要的材料 |

## 3. 复核并交付

```sh
python3 "$SKILL_DIR/scripts/build_reference_result.py" --input-dir /input --work-dir /app/work --output-dir /app/output --review /app/work/reference_review.json
```

打开最终稿件，核对原文未要求修改的段落、图表、脚注、附录和可编辑样式。若输入是多文件 TeX 工程，将仍需的原始子文件、图片、样式按相对位置复制到输出工程，核对改稿引用的 `references_synced.bib`，在题面要求时用可用引擎实际编译。若期刊自定样式、特殊脚注、复合域或版式超出脚本支持，继续按案例要求编辑实际成品并检查，保持最终证据和变更记录与成品相符。Word 输出带全篇 PDF 预览及可用时的首页 PNG。打开预览核对正文、书目和分页。

最后逐项比对案例契约。检查文中每处引文、每条文献、`case_id`、真实产物路径、来源定位、已审覆盖、未决问题和运行状态。对仍无法确定的条目保留具体限制。所有正式运行材料都写在 `/app/output`，正式案例与测评分由平台提供。最小调用和可复现示例见 `README.md`。
