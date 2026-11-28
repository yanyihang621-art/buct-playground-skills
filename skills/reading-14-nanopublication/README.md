# 阅读 14｜Nanopublication 断言拆解与证据绑定（`reading-14-nanopublication`）

> 由精读原文的 Agent 亲手完成原子断言拆解与条件限定；脚本专注**多格式摄取、内联表格还原、逐字引文核验、原子性/情态/因果/单位护栏、W3C 四图 RDF 序列化与契约对齐**。

## 1. 演进与设计取舍（v1 → v4）

- **v1（`104123`，62.33 分）与 v2（`113231`，56.67 分）**：v2 的正则全自动拆解与长链 `review` 在 DeepSeek Harness 下容易耗尽预算，且带有明显模板拼装痕迹（平台评语 *"push your agent for a more complete, genuinely-derived solution"*）。
- **v3（`627c385`）与 v4 核心升级**：
  1. **零截断任务简报与结构化摄取修复**：同步修复 `structured_sources.py` 的 `adapt_article()` 章节与表格渲染缺陷，全面支持 PDF、DOCX、HTML、LaTeX、JATS XML、CSV/TSV、BibTeX、JSON/JSONL、Markdown/TXT 与内联材料；`prepare.py` 输出完整 `work/case_brief.md` 与 `work/answer_template.json`。
  2. **精准语义与事实护栏**：剔除 `distinct_units >= 2` 误报，新增 `over_broad_assertion`（>280 字符或多句）、`causal_overclaim`（关联词不得升级为 `causes`）、`unit_mismatch`（物理单位族一致性）、数量级等价数值校验（`1M` ↔ `100万`）及跨页续接引文精确匹配。
  3. **多维度冲突检测与安全 IRI**：除同主谓宾异极性冲突外，新增反义谓词冲突（`increases` ↔ `decreases`、`activates`/`improves` ↔ `inhibits`、`causes` ↔ `prevents`）与有向反序冲突（`outperforms`）；对含空格/特殊字符的断言 ID 与 DOI 进行规范化 IRI 编码，多篇输入隔离每篇 `source_metadata`。
  4. **四图全覆盖导出与递归契约投影**：同时输出 `nanopubs.json`、`nanopubs.trig`（含 `@prefix`）、`nanopubs.nq`、`nanopubs.jsonld`（检测到 `rdflib` 时交叉验算三者四元组数完全一致）及展示**全部证据引文**的 `nanopubs.md`；支持扁平及嵌套四图（`head`/`assertion`/`provenance`/`pubinfo`）封闭/开放契约无损投影。

## 2. 快速执行流程

```bash
export PYTHONDONTWRITEBYTECODE=1
S=/skills/weekly-skill
[ -f "$S/scripts/prepare.py" ] || S=/app/submission

# 1) 解析输入、生成 case_brief.md / answer_template.json 与保底 result.json
python3 "$S/scripts/prepare.py" --input /input --case /input/case.json --work /app/work/r14 --out /app/output

# 2) 按大纲检索或查看内联表格/原文页
python3 "$S/scripts/find.py" --work /app/work/r14 --section sec_1
python3 "$S/scripts/find.py" --work /app/work/r14 --tables
python3 "$S/scripts/find.py" --work /app/work/r14 --page 8 --raw

# 3) 写入 work/claims.json（可分片）与 work/answer.json 后校验与导出
python3 "$S/scripts/finalize.py" --case /input/case.json --work /app/work/r14 --out /app/output
```

## 3. 四图模型、受控谓词与可复用资源

- **22 个受控谓词**：`increases`, `decreases`, `improves`, `outperforms`, `comparable_to`, `differs_from`, `causes`, `prevents`, `inhibits`, `activates`, `associated_with`, `correlated_with`, `has_value`, `achieves`, `requires`, `enables`, `used_for`, `part_of`, `is_a`, `defined_as`, `produces`, `interacts_with`。
- **四图 IRI 结构**（`https://w3id.org/np/local/<paper_sha12>/<safe_claim_id>`）：
  - `/head`：`np:Nanopublication` 绑定 `/assertion`、`/provenance`、`/pubinfo`。
  - `/assertion`：物化 `rdf:Statement`（含 `polarity`、`modality`、`qualifiers`），仅当 `affirmative` 且 `asserted` 时附加直接三元组。
  - `/provenance`：`prov:wasDerivedFrom` 绑定全部最小引文、页码、锚点、章节、`nativeLocator`、`evidenceType` 与 `prov:wasQuotedFrom`。
  - `/pubinfo`：`dct:created`、`dct:creator`、`dct:license`（CC-BY-4.0）、`npv:contentHash` 与论文元数据。
- **可复用资源**：
  - 受控谓词与抽取规范：[schemas/predicates.yaml](schemas/predicates.yaml)、[schemas/extraction_rules.yaml](schemas/extraction_rules.yaml)
  - 字段字典与 Schema：[schemas/field_dictionary.yaml](schemas/field_dictionary.yaml)、[schemas/claims.schema.json](schemas/claims.schema.json)、[schemas/answer.schema.json](schemas/answer.schema.json)
  - 22 条跨学科合成断言样例与示例输入（非正式案例）：[examples/example_20_claims.json](examples/example_20_claims.json)、[examples/example_case.json](examples/example_case.json)、[examples/example_claims.json](examples/example_claims.json)

## 4. 适用范围、限制、故障排查与维护

- **依赖要求**：Python ≥ 3.10；`PyMuPDF (fitz)`（推荐）、`pdfplumber`、`pypdf`、`python-docx`、`rdflib`（可选：无 `rdflib` 时使用零依赖纯标准库 N-Quads/TriG/JSON-LD 生成器与正则校验）。
- **故障排查**：
  - `unverified_quote`：从 `reader/paper_*.md` 原样复制；表内数据配对条件文本时分开放入 `evidence[]`。
  - `rdf_export_inconsistent`：检查 `claim.id` 与 `doi` 格式；`finalize.py` 会自动执行 IRI 安全编码与 DOI 规范化。
- **版本与维护**：`v4.0.0` · BUCT Product Arena Team (`reading-14-nanopublication`) · License: MIT / CC-BY-4.0。

## 5. 本地自测与基准

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/self_test.py --out-dir /tmp/reading14-selftest --write-benchmark
```
实测结果记录于 [benchmarks/validator_benchmark.md](benchmarks/validator_benchmark.md)。
