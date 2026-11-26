---
name: writing-04-references
description: "Edit citations in an academic manuscript, reconcile multi-format reference libraries, renumber after edits, verify sentence-level evidence, and deliver the editable draft and a reproducible reference audit. Use for citation placement, deduplication, source verification and style conversion."
---

# 参考文献与引用管理

你是本案例的引文编辑。阅读任务约束、稿件、文献库和可用原文。交付一份作者能继续编辑的稿件、一份与正文同步的文献表，以及能从每处改动追溯到材料的证据。使用案例指定的语言、期刊体例和文件名。以下流程由运行当前案例的 Agent 执行。

## 1. 建立初稿与证据索引

读 `/input/case.json`、任务说明和材料清单。记录目标样式、必须产物和 `output_contract` 的字段及类型。第一遍生成取证材料，后续只在最终稿上运行 Word 渲染、TeX 编译和所需的 DOI 核验。

```sh
SKILL_DIR=/skills/weekly-skill
[ -f "$SKILL_DIR/scripts/build_reference_result.py" ] || SKILL_DIR=/app/submission
python3 "$SKILL_DIR/scripts/build_reference_result.py" --draft --input-dir /input --case /input/case.json --work-dir /app/work --output-dir /app/work/reference_draft
```

打开初稿目录的 `result.json`、`evidence/citation_map.json` 和 `evidence/qa_report.json`。先解决会阻断同步的原编号、稿件选择或条目冲突，再查看改稿后的论断。若保留了旧编号，`evidence/retained_citations.json` 仍列出能够查到来源的每个位置，结合未解析项核对原表。支持性文件的页码、段落和正文在 `/app/work/references/evidence_passages.json`。两份稿件角色不清时依据任务说明在 review 中指定 `manuscript_file` 与 `bib_files`。对漏读的材料可用相应的 Office/PDF/OCR 工具读取并记录位置。

## 2. 逐条编辑和判断

先打开 `evidence/claim_coverage.json`，对照全文处理未插引的论断。每个分句给出行列坐标、实际覆盖它的引文、相邻子句的引文，以及可回查的候选原句。读 `source_candidates[].checks_for_reader` 中的同单位数值差异和方向提示，核准比较条件后才决定改稿。对作者自身方法或本研究结果，优先核对随稿材料，避免误引外部工作。

优先核对含方向、单位、数量或适用范围的论断。能定位到原文摘录时，比较同一指标在相同条件下的取值，处理草稿和证据之间的实质差异。已给出的摘要中若用不同群体、绝对量或相对变化，按真实限定报告。遇到冲突应改定性陈述、修正引文或将结论转入待核结果。核对 `evidence/final_claim_audit.json` 中仍待处理的条目。

Word 存在 Zotero、EndNote 或原生文献域时，读取 `evidence/word_citation_audit.json`，核对域指令、缓存、脚注和尾注各自定位。可安全映射的域转为稳定、可编辑的引文文本，动态域原稿另保存在 `evidence/`。若要求继续使用文献管理器刷新，明确哪些域仍需在原稿中核对。多文件 TeX 读取 `evidence/tex_project_report.json`，检查子章节、图注、附录的源路径、编译后的书目键和未解析数。检查原文页码定位及叙述式作者引文在改后仍然存在。

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
| `claim_resolutions` | 对漏引候选逐项写 `claim_id`（分句 ID）、逐字 `claim_quote`、`decision`、`reason`。`own_result` 附独立输入材料的 `source_file`、`source_quote` 及必要时 `page`。`shared_citation` 附临近且已核验的 `occurrence_index`、`cite_key`、`source_quote`。`not_required` 仅适用于低优先级并需理由。用 `insufficient_evidence` 明确保留待核事项 |
| `contract_values` | 根据案例专属字段的定义及类型写真实内容。输出文件路径须指向已经生成的产物 |
| `output_names` | 案例指定文件名时，用产物键映射到输出目录内的相对文件名，例如将 `editable_docx` 映射为 `final/revised.docx` |
| `docx`, `uncertainties` | 案例指定 Word 时用 `true`；需人工确认的条目给出位置、问题和需要的材料 |

## 3. 复核并交付

```sh
SKILL_DIR=/skills/weekly-skill
[ -f "$SKILL_DIR/scripts/build_reference_result.py" ] || SKILL_DIR=/app/submission
python3 "$SKILL_DIR/scripts/build_reference_result.py" --input-dir /input --work-dir /app/work --output-dir /app/output --review /app/work/reference_review.json
```

打开最终稿件，核对段落、图表、脚注、附录和可编辑样式。核验 TeX 工程的真实编译与引用报告。若期刊自定样式或特殊 Word 域超出脚本支持，在成品中完成所需编辑并更新证据。APA 交付检查期刊名、卷号斜体、期号正体和 Word 悬挂缩进。打开实际 PDF 预览核对正文、书目和分页。

最后逐项比对案例契约。检查文中每处引文、每条文献、`case_id`、真实产物路径、来源定位、已审覆盖、未决问题和运行状态。对仍无法确定的条目保留具体限制。所有正式运行材料都写在 `/app/output`，正式案例与测评分由平台提供。最小调用和可复现示例见 `README.md`。

当案例以 `additionalProperties: false` 限定字段时，`result.json` 按字段集合交付，扩展审计在 `evidence/contract_audit.json`。使用 `evidence/artifact_manifest.json` 的相对路径逐一打开交付文件。
