---
name: reading-15-knowledge-graph
description: 构建可查询的多论文知识图谱。依据 JSON/JATS 清单、PDF 或文本提取有向关系，按论文解释缩写和实验实例，保留每条边的原文证据，交付可编辑图谱与查询结果。
---

# 阅读 15 · 文献知识图谱

在 Harbor 中先读案例问题，再运行：

```sh
python3 -B /skills/weekly-skill/scripts/run_case.py
```

这条命令生成初图、执行查询、导出 GraphML 与预览，并校验最终结果。用 `/app/work/graph/review_plan.json` 核对遗漏段落和两篇论文的来源。按下文格式记录修订，用 `--reviewed /app/work/reviewed_claims.json` 重新运行。若案例提出特定问题，先将问题转为查询，传入 `--query-plan /app/work/queries.json`。该文件是参数对象数组，例如 `[{"query_type":"paper_evidence","source_id":"实际论文ID"}]`。检验有向关系时使用 `relation_evidence_probe` 并指定 `direction: "forward"`、`relation`、`entity_a` 与 `entity_b`。结果进入同一份可重放查询记录。用 `--answers` 写入需解读的案例字段，`--base-kg` 读取增量任务的原图。查看 `/app/work/graph/validation_report.json` 的来源与查询核验结果。自定义目录使用 `--input`、`--work`、`--out`。

先读 `/input/task.md` 和 `/input/case.json`。记下本案指定论文、实体和关系类型、需回答的问题、交付文件以及 `output_contract`。Harbor 中使用 `/app/work` 保存中间材料，最终文件写入 `/app/output`。

## 建立可回查的语料和初图

```bash
export PYTHONDONTWRITEBYTECODE=1
SKILL_DIR=/skills/weekly-skill
if [ ! -f "$SKILL_DIR/scripts/ingest_corpus.py" ]; then SKILL_DIR=/app/submission; fi
S="$SKILL_DIR/scripts"
mkdir -p /app/work /app/output
python3 "$S/ingest_corpus.py" --input /input --case /input/case.json --out /app/work/ingested
python3 "$S/build_kg.py" --corpus /app/work/ingested/corpus.json --out /app/output --review-out /app/work/review_plan.json
```

逐篇检查 `corpus.json` 的来源清单、原始文件、DOI、段号、缺失材料和重复格式记录。修订案例以每篇的 `source_id`、JATS 段号和短引定位。`material.json` 的根元数据属于第一篇。核对行内公式、上下标时回到对应的原始 JATS/XML。PDF 输入可使用真实页码，DOCX 和 JSON 保留各自的段落定位。

## 核对决定答案的边

从本案要求的查询倒推需要哪些实体和边。回到各自论文，逐条检查主客体、关系方向、单位和实验条件，以及原句是本研究结论、引用他人工作还是推测。重点检查同形缩写、材料或方法名相同但物理样品、模型、数据集不同的情况。记录同一概念的合并依据以及应保持独立的实例。查看 `abbreviation_definitions` 和 `do_not_merge_records`。

读 `/app/work/review_plan.json`。每篇最多优先列出十段原文，同时列出该篇完整的分节段号目录。把案例问题对应到各篇的研究对象、方法、结果和作者写明的限制。若优先段未覆盖问题，到 `corpus.json` 按目录查回全文，直到覆盖能回答的各项要求。核对每个进入答案的三元组，特别留意被动语态中的施事、否定结果、文献转述、线性化公式和统计条件。对缺少正文依据的关系，按其具体来源和原因记录在不确定性中。

若规则遗漏或误读一个段落，将该段全部已复核关系保存到 `/app/work/reviewed_claims.json`。文件结构为 `{"paragraphs":[{"source_id":"…","paragraph_id":"…","claims":[…]}]}`。每条 claim 包含原文中的 `subject`、`object`、两端的 `subject_type`、`object_type`、关系 `relation`、同时支持两端及关系的逐字短引 `quote_snippet`、`evidence_strength` 和原文支持的 `conditions`。类型和关系参考 [图谱词表](schemas/kg_ontology.yaml)。指定段落的清单会替换该段的规则抽取，空清单可撤销该段误抽的关系。

```bash
python3 "$S/build_kg.py" --corpus /app/work/ingested/corpus.json --claims /app/work/reviewed_claims.json --out /app/output
```

仅在有复核文件时执行上条命令。有已交付的旧图且本案要求增量更新时，可在构图命令中传入 `--base-kg`。

对案例限定的小主题，可以在复核文件的顶层设 `"mode": "reviewed_only"`。逐篇收录足以支撑本案问题的全部已核实关系，脚本仅发布收录关系和由它们导出的实例边。需要保留其余段落的自动抽取时，使用默认的 `replace_paragraphs`。每次运行会记录复核段号与采纳条数。

## 执行问题、导出和验收

```bash
O=/app/output
python3 "$S/query_kg.py" --kg "$O/kg.json" --run-default-suite --out "$O/executed_queries.json"
```

题面要求至少 3 项实际查询。读回每项的参数、命中、原文短引和来源，确保两篇论文均有据可查。若案例指定具体问题，再用 `--query-type` 及 `--entity`、`--source-id` 或 `--entity-a`、`--entity-b` 运行相应查询，合并真实返回的 `queries` 数组并给各条唯一 `query_id`。双义缩写查询要带论文来源或明确节点 ID。对于无直接证据的问题，保留实际查询结果和缺口。

检验“甲是否降低乙”时，对 `relation_evidence_probe` 传 `--entity-a 甲 --entity-b 乙 --relation decreases --direction forward`，按需指定 `--source-id`。读回 `evidence_strength` 与 `has_direct_observation` 区分实测、假设与转述。保留问题中的单位及适用条件。需要查询两个节点之间任意方向的关系时可用 `--direction either`。

```bash
python3 "$S/export_kg.py" --kg "$O/kg.json" --queries "$O/executed_queries.json" --out "$O"
python3 "$S/build_kg_result.py" --case /input/case.json --kg "$O/kg.json" --queries "$O/executed_queries.json" --graphml "$O/kg.graphml" --markdown "$O/kg_report.md" --out "$O/result.json"
python3 "$S/validate_kg.py" --case /input/case.json --result "$O/result.json" --kg "$O/kg.json" --corpus /app/work/ingested/corpus.json --queries "$O/executed_queries.json" --graphml "$O/kg.graphml" --out /app/work/validation_report.json
```

检查 `result.json`、GraphML、图谱预览和验证报告。若 `output_contract` 含需要解读的字段，先按真实查询结果撰写带来源的答案，用 `--override-json /app/work/case_answers.json` 重建结果并再验证。图谱、查询和来源字段应从各自上游步骤修正。无法核验的论文或关系在最终状态和不确定性中说明。
