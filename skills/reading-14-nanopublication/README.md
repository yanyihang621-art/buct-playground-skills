# 阅读 14｜Nanopublication 断言拆解与证据绑定（`reading-14-nanopublication`）

> 由阅读原文的 Agent 亲手完成原子断言拆解与条件限定，脚本专注**锚点索引、逐字引文校验、情态/极性/原子性检查、四图 Nanopublication RDF 序列化与契约投影**。

## 1. 为什么重构为 v3（v1 → v3 设计取舍）

- **v1（`104123`，官方终分 62.33）** 与 **v2（`113231`，官方终分 56.67）**：v2 的正则全自动拆解与长链 `review` 校验在 DeepSeek Harness 下容易耗尽预算，且规则提取的陈述句带有较强机械痕迹（平台评语 *"push your agent for a more complete, genuinely-derived solution"*）。
- **v3 核心思路（做减法）**：
  1. `prepare.py` 统一完成 PDF/Markdown/JATS/JSON 递归解析，写出分节阅读稿 `reader/paper_*.md`、索引 `index.json`、元信息 `meta.json` 及保底骨架 `result.json`。
  2. Agent 用 `find.py` 精读相关章节与表格，写出原子断言 `work/claims.json`（严格使用 22 个受控谓词，保留条件、否定与推测情态）及 `work/answer.json`。
  3. `finalize.py` 核验引文、数值、情态/否定一致性与复合句原子性，执行 Jaccard 去重与极性冲突检测，生成 W3C TriG / N-Quads / JSON-LD 四图导出（无外部 RDF 库零依赖运行，检测到 `rdflib` 时额外交叉验算四元组数一致），并精确对齐封闭/开放 `output_contract`。

## 2. 快速执行流程

```bash
export PYTHONDONTWRITEBYTECODE=1
S=/skills/weekly-skill
[ -f "$S/scripts/prepare.py" ] || S=/app/submission

# 1) 解析材料并写骨架 result.json
python3 "$S/scripts/prepare.py" --input /input --case /input/case.json --work /app/work/r14 --out /app/output

# 2) 按大纲检索或分节精读
python3 "$S/scripts/find.py" --work /app/work/r14 --section sec_1
python3 "$S/scripts/find.py" --work /app/work/r14 "may|not|Table"

# 3) Agent 写入 /app/work/r14/claims.json 与 /app/work/r14/answer.json 后校验与导出
python3 "$S/scripts/finalize.py" --case /input/case.json --work /app/work/r14 --out /app/output
```

## 3. 受控谓词与四图模型

- **22 个受控谓词**：`increases`, `decreases`, `improves`, `outperforms`, `comparable_to`, `differs_from`, `causes`, `prevents`, `inhibits`, `activates`, `associated_with`, `correlated_with`, `has_value`, `achieves`, `requires`, `enables`, `used_for`, `part_of`, `is_a`, `defined_as`, `produces`, `interacts_with`（定义见 [schemas/predicates.yaml](schemas/predicates.yaml)）。
- **四图 IRI 结构**（`https://w3id.org/np/local/<paper_sha12>/<claim_id>`）：
  - `/head`：`np:Nanopublication` 绑定 `/assertion`、`/provenance`、`/pubinfo`。
  - `/assertion`：始终输出物化 `rdf:Statement`（含 `polarity`、`modality`、`qualifiers`），仅当 `affirmative` 且 `asserted` 时额外输出直接三元组。
  - `/provenance`：`prov:wasDerivedFrom` 绑定最小逐字引文、页码、锚点、`evidenceType` 与论文 `prov:wasQuotedFrom`。
  - `/pubinfo`：`dct:created`、`dct:creator`、`dct:license`（CC-BY-4.0）与论文题录元数据。
- **字段字典与 Schema**：见 [schemas/field_dictionary.yaml](schemas/field_dictionary.yaml)、[schemas/claims.schema.json](schemas/claims.schema.json)、[schemas/answer.schema.json](schemas/answer.schema.json)。

## 4. 适用范围、限制与依赖

- **支持格式**：数字版 PDF（双栏重排/表格提取）、Markdown、TXT、JATS XML、结构化 JSON；支持加密/损坏/扫描件降级。
- **已知限制**：扫描件无 Tesseract OCR 时标记 `ocr_unavailable`；`finalize.py` 自带纯标准库 TriG/N-Quads/JSON-LD 序列化器与 N-Quads 语法校验，若运行环境未安装 `rdflib` 会在报告中注明 `rdflib_unavailable`。

## 5. 本地自测与示例（非正式案例）

- 最小示例与 20 条跨学科合成断言样例位于 `examples/`（均标注“示例，非正式案例”）。
- 运行自测与故障注入基准：
  ```bash
  PYTHONDONTWRITEBYTECODE=1 python3 scripts/self_test.py --out-dir /tmp/reading14-selftest --write-benchmark
  ```
- 实测报告见 [benchmarks/validator_benchmark.md](benchmarks/validator_benchmark.md)。
