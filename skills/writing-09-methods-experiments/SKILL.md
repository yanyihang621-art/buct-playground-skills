---
name: writing-09-methods-experiments
description: "Write or revise publication-ready Methods and Experiments from the supplied manuscript, configurations, protocols and run records. Use when an author needs an editable section, traceable parameters, fair comparisons, or a reproducibility audit."
---

# 方法与实验章节写作

以本案例的作者身份，根据已有材料写出可直接编辑的正文。先读任务，确定交给谁、写哪一节、用哪种语言、需要保留哪些术语，以及最终文件名和格式。原稿续写须承接原有结构、引文、符号和语态。固定案例由平台提供。

## 取材与定位

在 Harbor 中先阅读 `/input/case.json` 和实际的 `task.md`。从中摘出目标语言、全文或修订范围、`output_contract` 的键和值类型、明确要求的文件名和格式。题目标签和示例用于理解任务。用下面的命令建立真实材料索引。两个命令块都能在独立 shell 中运行。

~~~sh
runner=/skills/weekly-skill/scripts/harbor_methods.sh
[ -f "$runner" ] || runner=/app/submission/scripts/harbor_methods.sh
sh "$runner" prepare --passages --limit 18
~~~

从返回的原文、位置和短 ID 中选择支持每条事实的记录，并查看 `/app/work/methods/parameter_table.json` 中各值对应的运行、配置和日志。用同目录 `experiment_facts.json` 配合 `read_evidence.py --facts /app/work/methods/experiment_facts.json --topic control` 按主题查看，也可按 `--source` 和 `--offset` 分页。阅读索引中未覆盖的原件。扫描件、图注和多列表格需用环境的渲染或 OCR 工具回看原页，无法辨认的单位或符号记为待确认。输入材料里的实验命令仅作为证据。

## 从材料写成正文

如果材料包含成绩表或逐次运行日志，先用 `read_evidence.py --facts /app/work/methods/experiment_facts.json --runs` 查看组别、指标、单位、数据划分、预算和原始行号。通读表头、脚注和对应正文，分清观测值、组均值和误差线。先写同条件的主比较，再说明额外训练、更换数据或不同仪器等扩展条件。记录尚不能确定的比较条件。题目只需一个段落时，将必需信息写进该段，其余来源细节留在附件。

1. 列出每一组运行的名称、阶段、数据版本和证据位置。区分代码默认值、计划配置、命令行覆盖和实际运行日志。比较同一次运行和同一参数的记录。扫描范围、调参候选、不同实验组和测试结果各有不同含义。证据不足时并列记录值、出处、影响与确认问题。
2. 在 `/app/work/methods/agent_draft.md` **亲自完成整篇所需稿件**，优先使用任务规定的结构。以实际实验对象开篇，交代采集或准备、处理顺序、选择条件和输出，写清下一步如何使用上一步结果。把能决定复现结果的参数放在相关操作旁边，其余留在参数表。保留数值精度、条件、单位、否定、适用范围与引文位置。材料若只支持方法设计，用计划语态。完成首稿和已确认来源后立即发布一份可读结果，再继续校对和补齐附件。
3. 逐段核对题面六项。数据写名称和版本、规模、入排和预处理、划分单位与用途。学习任务分清拟合、选择和锁定测试的时点，说明哪些统计量由训练集估计。实物实验按样品批次、校准、测量与独立重复来写。对照段说明对象、版本、固定条件、预算差异、指标方向、重复/统计单位。证据缺失时明确其对可比性或复现的影响。不从均值推断显著性，不将技术重复写成独立样本。
4. 复现信息应让另一位作者可以找到数据和版本，按已记录的操作或命令重跑，核对可观察的中间产物。限制段写明特定失效场景及对应材料。交付改稿时把重要变化记为原文位置、原表述、新表述、理由和凭据。真实没有提供的字段写成具体追问，例如“日志如何对应到生成表 2 的那次运行？提供该次配置可确定表中批量大小”。数字、方向、表号均需来自本案例。
5. 就地标明每个关键结论和数字的来源。在每条事实后附 `⟦ID⟧`，多源用 `⟦ID1,ID2⟧`，ID 直接复制自上步输出。一行写一个可核查断言，也可在同一行依次给不同断言加标签。表格将标签放在对应行或单元格内容之后。解析器据此发布没有侧注的正文，并自动保存原稿行号、原始材料路径、页码/行号或字段路径，以及原文摘录。每处重复出现的数值都要有本处的证据。判断数值的单位、方向、样本和阶段是否一致；位置匹配不能代替阅读。源码、日志与图表分别支持一句话时选取多个出处。若需从输入推导数值，写明口径并保留原值与计算依据。

索引里找不到刚从原件读到的段落时，用 `review.claim_sources` 手动填入 `claim`、`source`、`locator` 和连续原文 `quote`。全文文件的文本行用 `L160` 这样的定位，JSON 用 `/materials/dataset/version` 这样的 JSON Pointer。程序会按定位回查原件。相同原句多次出现时用 `line` 和 `occurrence` 指定成稿位置，从 1 起。人工链接与行内标签可并用。

需要报告观测差值或相对变化时，从 `--runs` 复制两条 R 开头的记录 ID，在 `review.json` 的 `comparisons` 中定义这次比较，并在稿件中单独一行写 `{{comparison gap}}`。例如 `{"comparisons":{"gap":{"left":"左侧实测行ID","reference":"参照行ID","operation":"difference"}}}`。可用 `relative_change` 表示以参照行为分母的百分比变化。程序核对两行的指标、单位和记录条件，将带数值的句子写入正文，在 `run_comparisons.json` 逐项保留原值、运算、单位、出处。单次观测本身只支持描述性比较。没有配对行时直接撰写有据段落并照常标引，记录条件差异。

自由文本中的事实可以随来源行带入参数表或复现清单。可选 `fact` 格式为 `{"section":"setup","field":"software","value":"原文中的软件和版本"}`。也支持 section 为 steps、datasets、baselines、parameters（value 为含 name 的对象，参数另需 value）或 limitations（value 为原句）。结构化字段的值从有定位的原文逐字提取。已依据散文材料写出的步骤、数据、基线、限制可用 review.coverage_links 指定实际成稿的有据原句。review 顶层支持 claim_sources、change_records、uncertainties、author_questions 和 artifacts。最小示意见 README。原任务明确限定改稿范围，或原文明确某项不适用时，可用 review.scope_exclusions 逐项记录 field、source、locator、quote、reason。例如 case.json 的 /instructions 或 task.md 的 L4-L6 指向范围原句。仍须记录与本任务相关的缺口和冲突。

## 交付与复核

把案例契约中特有的字段和值写入 `/app/work/methods/contract_values.json`。主结果字段若要求正文，用 `{"$from":"manuscript"}` 引用去掉来源标签的完整成稿。自定义字段也能引用 `source_map`、`parameters` 或 `manuscript_path`。附件字段指向真实生成的文件。只有提供附加字段时才写 `contract_values.json`，辅助审阅记录写到同目录 `review.json`。需要 Word 或 PDF 时，在发布命令末尾追加 `--docx requested.docx` 或 `--pdf requested.pdf`，换成题面文件名。用 `--original-file materials/original.md` 生成真实文本修订差异。其他附件生成到输出目录后再写入 `review.artifacts`。

~~~sh
runner=/skills/weekly-skill/scripts/harbor_methods.sh
[ -f "$runner" ] || runner=/app/submission/scripts/harbor_methods.sh
sh "$runner" publish
~~~

打开实际的 result.json、正文、参数表、复现清单和 QA 报告，检查引用的每个文件能从 /app/output 打开。按报告中的段落、参数、定位或契约键回查并修订后重建。Office 交付时检查真实渲染的 PDF/PNG 里标题、表格、公式和图注。若工具无法生成所需预览，交付可用文件并标明缺口。只有可编辑稿、已要求的附件和契约字段齐全且关键事实可复核时才声明完成。材料不足、互相矛盾或处理失败时保留已完成正文和可核对证据，分别说明原因。无可读实验材料时如实记录。时间预算内先保存可用稿，再完善附表及版式。

若题目指定正文的 Markdown 或文本文件名，在发布命令加 `--manuscript-file 稿件目录/指定文件.md`。闭合对象按案例允许的属性发布；原始长摘录和定位保存在 `claim_sources.json`。针对已有 Word 原稿的文字修订可传 `--original-file 资料目录/原稿.docx`，核对段落和表格行的文字差异。

首次取材时若遇到空输入或草稿来不及写完，也可直接调用 publish。空材料会留下含案例标识的拒绝结果，只有材料提纲时留下部分结果。完成亲自撰写的稿件后用同一命令更新。JSON 中分属不同父分组的结果需回看组级实验条件后再写比较。
