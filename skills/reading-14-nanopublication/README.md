# 科研 Nanopublication 构建与证据核验

面向阅读14。输入一篇或多篇科研材料，输出逐条可核对的原子断言、最小引文、来源位置及 JSON、JSON-LD、TriG、N-Quads 和 Markdown。抽取器产生候选，Agent 结合案例问题补漏、裁去无关结论并审阅断言。案例独有的答案字段由 Agent 结合原文填写。

## 运行

在安装此 Skill 的目录使用 Python 3.10+。词表读取使用 PyYAML，PDF 处理使用 PyMuPDF。OCR 取决于运行环境是否具备识别引擎和所需语言包，Pillow 用于部分图像操作。RDF 导出使用标准库。

```sh
python3 -B scripts/run_case.py --input examples --case examples/example_case.json --work /tmp/np-example/work --out /tmp/np-example/output
```

输入为包内合成光学短文 `examples/sample_paper.md`。应输出两条断言。第一条保留低照度条件和 may，第二条为明确陈述。对照 `examples/example_result.json` 可检查句子、字段、源文件哈希与行号，生成时间随运行变化。生产路径及复核命令见 [SKILL.md](SKILL.md)。Harbor 中从 `/skills/weekly-skill/scripts/run_case.py` 启动，默认读取 `/input/case.json`。

## 输入与定位

| 材料 | 可复核位置 |
|---|---|
| 文字型 PDF | 物理页码、可读印刷页码、文本块或表格 ID、坐标；存在单元格坐标时定位到该格 |
| Markdown、TXT | 文件哈希、原始行号、段落 ID；Markdown 规则表格补充数据行序号与列名 |
| 结构化 JSON | 文献集合、章节、段落、句子和规则表格；保留 JSON Pointer、已有段号与作者给出的页号 |
| JATS XML | 文章元数据、摘要和正文段落、规则表格；记录 XML 元素路径与原段号 |
| 案例内联材料 | 从声明的材料字段读取，定位回 case.json 内的段落和字段；正文可与外部文献共用 |

`ingest_manifest.json` 列出每个材料的 `paper_id` 和解析文件。原文无法访问、附件未找到、格式无法识别、扫描页读不出正文时，应沿记录回到对应文件检查。复杂表头、合并单元格、图片与跨段证据需按原材料核实；各条结论可以单独标注局限。缺少文献版本时记录 `not_reported`。

## 从候选到交付

1. 阅读任务和材料，确认目标段落、研究对象、变量、比较组、单位及限定条件。通读每项问题涉及的原段和图表，把合成示例与案例材料分开。
2. 拆分独立可判真的关系。按 [关系词表](schemas/relation_vocabulary.yaml) 核对主客体和方向。保留陈述的肯定或否定、推测或确定、原文引用归属。对重复结论合并其定位，对条件或证据不同的结论保留差异。
3. 若候选遗漏了表述，可按 [SKILL.md](SKILL.md) 的简式复核行补录短引、原块 ID、主谓宾、条件、陈述和置信度依据。每篇修订清单可混用旧完整记录和新简式行。程序从原材料派生位置、来源、图标识和各格式。表格输出的行号从首个数据行记为 1，单元格原值和单位以列标题及单元格共同解释。
4. 查看 `/app/output/result.json`、案例指定产物与 `/app/work/nanopubs/validation_report.json`。修正影响任务的缺口，再次运行 `run_case.py --reviewed ... --answers ...`。三个 RDF 格式描述同一命名图集合。限定、否定、推测和引用关系保留其陈述强度。
5. 配套资源包括 [字段字典](schemas/nanopub_field_dictionary.yaml)、[JSON Schema](schemas/nanopublication.schema.json) 和 [20 张合成示例卡](examples/example_20_nanopubs.json)。卡片涵盖推测、无显著影响、术语、实验比较等，逐条提供原句字段的 JSON Pointer。源代码按摄取、拆解、序列化、结果构建、校验拆分，均在 `scripts/` 内。

## 验证

```sh
python3 -B scripts/self_test.py --out-dir /tmp/np-tests --compare-baseline
```

| 合成回归 | 核验内容 |
|---|---|
| N1 | PDF 复合句、共享条件、表格数值及 6 项故障注入；本次 7 条断言、249 个 N-Quads 四元组 |
| N2 | 重复位置合并；相同范围的相反观察保留两条断言并记录冲突 |
| N3 | 加密 PDF 拒绝，无法取得文本的扫描 PDF 返回无法核验 |
| N4 | 混合格式、推测/肯定/否定、逐篇覆盖、过期 RDF 和强度篡改检测 |
| N5 | JSON、JATS、内联论文共 3 份；审核补全后 7 条断言，3 个数据单元格；拦截虚构数值、移错表格行和伪造引文 |

详细运行记录写入指定目录。对照开关只对同一本地合成输入比较保留整段文字的简单规则与本流程。赛事基础 Agent 的增益和成绩由平台在固定案例上测量。
