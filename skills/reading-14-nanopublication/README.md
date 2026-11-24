# reading-14-nanopublication — 科研 Nanopublication 构建与核验 Skill

面向 **玻尔产品周赛 · 北化分站赛（第一期）｜阅读14：Nanopublication** 的自包含、零外部 RDF 库依赖 Agent Skill。围绕 **“拆得细、对得上、追得回、读得懂”** 四大核心指标，将科研论文（PDF / Markdown / TXT）拆解为符合标准 4-图（`Head`、`Assertion`、`Provenance`、`PubInfo`）规范的原子断言，输出 JSON、W3C JSON-LD 1.1、W3C TriG、W3C N-Quads 与 Markdown 审阅表，并通过 6 道确定性质量门控自动拦截过宽断言、非法谓词、极性矛盾、伪造锚点、主体混淆及元数据缺失。

---

## 1. 目录与资源结构

```text
reading-14-nanopublication/
├── SKILL.md                              # Harbor / Agent 执行指引
├── GITHUB_URL.txt                        # 公开 GitHub 链接声明
├── README.md                             # 文档、受控词表、序列化说明与自测报告
├── schemas/
│   ├── relation_vocabulary.yaml          # 18 个受控科研谓词词表（含极性、URI、互斥关系）
│   ├── nanopub_field_dictionary.yaml     # 4-图字段字典、5 类证据类型与 6 道质量门控规范
│   └── nanopublication.schema.json       # nanopubs.json JSON Schema
├── examples/
│   ├── example_case.json                 # 最小输入样例（标注仅作示例）
│   ├── example_result.json               # 最小输出样例（标注仅作示例）
│   └── example_20_nanopubs.json          # 20 条跨学科可复用 Nanopublication 合成样例（标注仅作示例）
└── scripts/
    ├── __init__.py
    ├── pdf_utils.py ... parse_pdf.py     # 自包含 PDF 解析底座
    ├── ingest_source.py                  # PDF / MD / TXT 统一摄取
    ├── decompose_assertions.py           # 复合句/因果句原子拆解、表格断言、证据分级、去重与冲突检测
    ├── serialize_nanopubs.py             # 零依赖 JSON-LD / TriG / N-Quads / Markdown 序列化与 N-Quads 语法校验
    ├── build_nanopub_result.py           # 对齐 /input/case.json output_contract 的 result.json 构造器
    ├── validate_nanopubs.py              # 6 道确定性质量门控审计器
    └── self_test.py                      # 3 套合成夹具 + 6 项负向故障注入门控测试 + 基线对照
```

---

## 2. 受控关系词表与 4-图标准

- **18 个受控科研谓词**（[`schemas/relation_vocabulary.yaml`](schemas/relation_vocabulary.yaml)）：`causes`、`increases`、`decreases`、`inhibits`、`catalyzes`、`improves`、`outperforms`、`achieves_metric`、`correlates_with`、`associates_with`、`has_property`、`measured_at`、`requires_condition`、`restricted_to`、`fails_under`、`does_not_affect`、`composed_of`、`equivalent_to`，每个谓词均绑定标准 URI（OBO RO / SIO / OWL / npv）、极性与互斥冲突集合。
- **5 类证据分级**：`direct_experimental`、`direct_tabular_or_figure`、`direct_textual`、`indirect_inference`、`cited_prior_work`。
- **内容寻址标识符**：`urn:nanopub:sha256:<16位摘要>` 支持跨段落重复断言自动合并（`deduplication_log`）。
- **零外部 RDF 库依赖**：不依赖 `rdflib`，纯 Python 标准库即可生成并通过 W3C N-Quads 语法解析校验。

---

## 3. CLI 完整调用步骤

```bash
# 1. 摄取论文
PYTHONDONTWRITEBYTECODE=1 python3 scripts/ingest_source.py \
  --input /input --case /input/case.json --out /app/work/ingested --ocr auto --crops

# 2. 原子断言拆解、证据绑定与去重冲突检测
PYTHONDONTWRITEBYTECODE=1 python3 scripts/decompose_assertions.py \
  --manifest /app/work/ingested/ingest_manifest.json --out /app/work/nanopubs

# 3. 序列化为 JSON-LD / TriG / N-Quads / Markdown
PYTHONDONTWRITEBYTECODE=1 python3 scripts/serialize_nanopubs.py \
  --nanopubs /app/work/nanopubs/nanopubs.json --out /app/work/nanopubs

# 4. 构造 /app/output/result.json
PYTHONDONTWRITEBYTECODE=1 python3 scripts/build_nanopub_result.py \
  --case /input/case.json --nanopubs /app/work/nanopubs/nanopubs.json \
  --manifest /app/work/ingested/ingest_manifest.json \
  --trig /app/work/nanopubs/nanopubs.trig --jsonld /app/work/nanopubs/nanopubs.jsonld \
  --nquads /app/work/nanopubs/nanopubs.nq --markdown /app/work/nanopubs/nanopubs.md \
  --out /app/output/result.json

# 5. 运行 6 道质量门控校验
PYTHONDONTWRITEBYTECODE=1 python3 scripts/validate_nanopubs.py \
  --case /input/case.json --result /app/output/result.json \
  --nanopubs /app/work/nanopubs/nanopubs.json \
  --manifest /app/work/ingested/ingest_manifest.json \
  --nquads /app/work/nanopubs/nanopubs.nq --out /app/work/validation_report.json
```

---

## 4. 本地可复现自测与基线对照

> 以下结果来自运行本目录自带合成测试集（`scripts/self_test.py`）的真实本地观测输出，**仅用于本地工程回归验证，不代表任何官方赛题成绩**。

复现命令：
```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/self_test.py --out-dir /tmp/reading14_test --compare-baseline
```

本机实测输出（`all_passed: true`）：

| 测试夹具（合成） | 覆盖场景 | 实测结果 |
|---|---|---|
| `n1_catalysis_nanopub.pdf` | 复合因果句拆解、前导条件 `573 K / 3.0 MPa` 传播、Table 1 表格断言、4 类证据类型区分、DOI/年份溯源、N-Quads 语法校验、**6 项负向故障注入质量门控测试** | `pass: true`；产出 `7` 条原子 Nanopublication（`112` 条 N-Quads）、`anchor_grounding_rate: 1.0`；6 项负向故障注入（过宽断言 / 非法谓词 / 断言与证据矛盾 / 伪造锚点 / 主体混淆 / 缺失元数据）全部成功拦截 |
| `n2_markdown_paper.md` | Markdown 摄取、重复断言合并（`dedup_merges`）、跨断言冲突检测（`increases` vs `decreases`） | `pass: true`；`nanopub_count: 2`，`dedup_merges: 1`，`conflicts: 1` |
| `n3a_encrypted.pdf` / `n3b_scanned.pdf` | 加密拒答与无文字层扫描件降级边界 | `pass: true`；加密返回 `refused`，扫描件返回 `unverifiable`（零伪造断言） |

与“整段话塞入单条断言”基线在同一合成夹具 `N1` 上的对照：
- **原子 Nanopublication 数量**：基线 `1` → Skill `7`
- **受控词表谓词种类**：基线 `0` → Skill `5`
- **区分的证据类型数**：基线 `1` → Skill `4`（`direct_experimental` / `direct_tabular_or_figure` / `indirect_inference` / `cited_prior_work`）
- **通过语法校验的 W3C N-Quads 四元组数**：基线 `0` → Skill `112`

---

## 5. 已知限制

1. 确定性正则拆解主要覆盖常见科研因果/调控/指标/条件句式，对于长隐喻或复杂被动倒装句可能漏拆（会在 `uncertainties` 中提示 `no_assertions_extracted` 或交由 Agent 结合原文补充 `--override-json` 再经 6 道门控校验）。
2. 目前内置的 `verify_nquads_syntax` 面向本工具生成的 IRI + 字面量 N-Quads 子集；若外部注入空白节点（blank node `_:b0`）需同步扩展语法正则。
