# reading-15-knowledge-graph — 文献知识图谱构建 Skill

面向 **玻尔产品周赛 · 北化分站赛（第一期）｜阅读15：文献知识图谱构建** 的自包含 Agent Skill。围绕 **“抽得准、连得对、对得齐、查得到、回得去”** 五大核心要求，并严格适配题面修订后的 **双篇独立论文 JATS/JSON 清单（`corpus-manifest.json` / `primary-material.json` / `secondary-material.json` / `secondary-publisher.xml`）** 与标准 PDF 语料。

---

## 1. 目录与模块说明

```text
reading-15-knowledge-graph/
├── SKILL.md                              # Harbor / Agent 运行主指引
├── GITHUB_URL.txt                        # 公开 GitHub 仓库位置声明
├── README.md                             # 完整使用说明、本体定义与自测报告
├── schemas/
│   ├── kg_ontology.yaml                  # 实体类型、有向关系词表、证据强度分级与不合并保护策略
│   ├── kg_schema.json                    # kg.json 输出 JSON Schema
│   └── query_templates.yaml              # 5 类可复用图谱查询模板
├── examples/
│   ├── example_case.json                 # 最小输入样例（标注仅作示例）
│   ├── example_result.json               # 最小输出样例（标注仅作示例）
│   └── example_kg_queries.json           # 查询样例（标注仅作示例）
└── scripts/
    ├── __init__.py
    ├── pdf_utils.py ... parse_pdf.py     # 自包含 PDF 解析底座
    ├── ingest_corpus.py                  # JATS XML / JSON 清单 / PDF / MD 统一摄取 + 同篇多格式防重
    ├── build_kg.py                       # 实体关系抽取、保守跨篇对齐与不合并实例保护、增量更新
    ├── query_kg.py                       # 5 类真实图谱查询（共享概念、邻域子图、指标对照、冲突、否定探针）
    ├── export_kg.py                      # 零依赖导出 GraphML XML、Graphviz DOT 与 Markdown+Mermaid 报告
    ├── build_kg_result.py                # 对齐 /input/case.json output_contract 的 result.json 构造器
    ├── validate_kg.py                    # 确定性验收审计器（分篇 DOI 隔离、禁伪造页码、100% 锚点落地、XML 校验）
    └── self_test.py                      # 4 套合成夹具 + 3 项负向故障注入 + 基线对照
```

---

## 2. 核心工程特性

1. **严格的分篇来源隔离与 JATS 定位**：独立绑定每篇文献的 `source_id`、`doi`、`title`、`authors`、`source_hash`；对 JATS/JSON 段落仅使用 `paragraph_id`（`pdf_page: null`），绝不伪造 `pdf_page: 1`。
2. **同篇多格式防重**：检测相同内容哈希或相同 DOI 的多格式文件（如 `.md` + `.json`），避免误把同篇多格式当作跨篇证据。
3. **保守实体对齐与“不得合并”保护（`do_not_merge_records`）**：仅合并明确的缩写/大小写抽象概念；当通用材料/方法术语（如 `Cu-In2O3`）在不同文献中具有不同实验条件或指标时，保留分篇实例节点（`MaterialInstance`）并显式记录原因。
4. **增量更新保留编号（`--base-kg`）**：追加新论文时 100% 保留既有 `node_id` 与 `edge_id` 并输出 `incremental_changelog`。

---

## 3. 本地可复现自测与基线对照

> 以下结果来自运行本目录自带合成测试集（`scripts/self_test.py`）的真实本地观测输出，**仅用于本地工程回归验证，不代表任何官方赛题成绩**。

复现命令：
```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/self_test.py --out-dir /tmp/reading15_test --compare-baseline
```

本机实测输出（`all_passed: true`）：

| 测试夹具（合成） | 覆盖场景 | 实测结果 |
|---|---|---|
| `k1_revised_jats_corpus` | 双篇独立 JSON + JATS XML 清单、`CO2RR` 跨篇合并、`Cu-In2O3` 不同条件不合并保护、5 个真实查询、GraphML XML 校验、**3 项负向故障注入（伪造 `pdf_page=1` / 泄漏 paper_1 DOI / 伪造 `paragraph_id`）** | `pass: true`；`13` 节点 / `7` 边；`evidence_grounding_rate: 1.0`（24/24）；`graphml_valid: true`；`do_not_merge_count: 1`；3 项负向故障全部成功拦截 |
| `k2_incremental_update` | 基于 Paper 1 底图增量合入 Paper 2 | `pass: true`；`all_base_node_ids_preserved: true`（3/3 保留），新增 3 节点 / 2 边 |
| `k3_duplicate_format_guard` | 同篇 `.md` + `.json` 重复格式拦截 | `pass: true`；`paper_count: 1`，`duplicate_formats_skipped: 1` |
| `k4_encrypted_and_scanned` | 加密拒答与扫描件无文字层降级 | `pass: true`；加密 `refused`、扫描件 `unverifiable`（0 节点/边） |

与“扁平共现抽取”基线在 `K1` 上的对照：
- **跨篇不合并保护检查**：基线 `0` → Skill `1`
- **JATS 段落锚点落地率**：基线 `0.0` → Skill `1.0`
- **实际可执行图谱查询数**：基线 `0` → Skill `5`
- **有效 GraphML XML 导出**：基线 `false` → Skill `true`
