# 阅读 15 · 文献知识图谱构建

这个 Skill 将多篇论文整理为可查询的有向图。每条实质关系保留论文 ID、原段号、原文短引和证据强度。完整命令见 [SKILL.md](SKILL.md)。

## 输入和交付

- 输入可以是嵌套的双论文清单、JATS/XML、逐段 JSON、PDF、DOCX 或 Markdown/TXT。按清单所在目录解析相对路径。合并索引只用于补充有明确来源的段落。用 DOI 和正文比对识别同篇材料的不同格式。
- 对有竞争释义的缩写保留逐篇定义。可用 `--source-id` 查询缩写在特定论文中的含义。无来源限定且存在歧义的查询会列出候选。跨篇样品和实验条件分别记录。
- `kg.json` 是可复查的图谱数据，`executed_queries.json` 存储实际参数和返回值。另提供可编辑 GraphML、DOT 与 Markdown 报告。有可展示关系且具备渲染依赖时，`graph_preview.png` 展示带来源段号的有向关系，并标明覆盖数量。
- JSON、JATS、DOCX 和纯文本来源保留段号或行号。PDF 来源可回查物理页码及文本块。未报告、缺失或有冲突的内容记录在来源清单及不确定性字段。

## 复用与验收

图谱生成使用 Python 3、PyYAML 和 PyMuPDF。PDF/OCR 能力以当前环境可用的软件为准。用 [图谱词表](schemas/kg_ontology.yaml) 选择实体和关系类型，用 [查询模板](schemas/query_templates.yaml) 选择可回放的问题。读者可以提交逐段核过的关系修订，再运行相同查询观察答案变化。追加语料时可用 `build_kg.py --base-kg` 保留旧节点编号。

在本目录下运行以下命令，输出目录选在 Skill 包之外。

```bash
python3 -B scripts/self_test.py --out-dir ../reading15-test-output --compare-baseline
python3 -B scripts/test_corpus_inputs.py -v
python3 -B scripts/contract_regression.py
python3 -B scripts/test_relation_reading.py -v
```

合成案例覆盖两篇独立论文的缩写冲突、正反序读入、查询结果重放、增量更新、对段落的修订、图谱导出、加密与扫描 PDF。输入回归覆盖嵌套清单中重名的文件、同文不同 DOI、合并索引补充缺失论文、DOCX/JATS 原始定位以及损坏文件。具体指标写入本地生成的 `self_test_report.json`。

## 可运行的最小示例

[示例案例](examples/example_case.json) 指向同目录两篇虚构的光学材料。下面的命令从原段落生成图谱，并回查滤光片与光谱漂移的关系。

```bash
python3 -B scripts/ingest_corpus.py --input examples --case examples/example_case.json --out ../reading15-demo/work
python3 -B scripts/build_kg.py --corpus ../reading15-demo/work/corpus.json --out ../reading15-demo/output --review-out ../reading15-demo/work/review_plan.json
python3 -B scripts/query_kg.py --kg ../reading15-demo/output/kg.json --query-type relation_evidence_probe --source-id bench --entity-a "reference filter" --entity-b "Spectral drift" --direction forward --relation decreases --out ../reading15-demo/output/forward.json
```

正向查询命中 `bench/a1` 的被动句，反向查询返回零条。[示意输出](examples/example_result.json) 含图谱、四项执行结果和原文定位，[查询参数](examples/example_kg_queries.json) 可用于重放。默认规则擅长带明确谓词的英文句子。多句指代、复杂数值限定和其他语言的段落可用逐段复核接口编入图谱。
