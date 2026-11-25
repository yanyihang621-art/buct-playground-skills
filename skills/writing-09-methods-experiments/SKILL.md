---
name: writing-09-methods-experiments
description: "Write or revise publication-ready Methods and Experiments from the supplied manuscript, configurations, protocols and run records. Use when an author needs an editable section, traceable parameters, fair comparisons, or a reproducibility audit."
---

# 方法与实验章节写作

以本案例的作者身份，根据已有材料写出可直接编辑的正文。先读任务，确定交给谁、写哪一节、用哪种语言、需要保留哪些术语，以及最终文件名和格式。原稿续写须承接原有结构、引文、符号和语态。固定案例由平台提供。

## 取材与定位

在 Harbor 中用下列命令定位工具和建立证据索引。每个新 shell 中重新设置这三个变量。本地运行时将路径换成实际目录。若 task.md 位于工作目录，读该处。打开 /input/case.json，特别检查嵌套材料、原始 output_contract、目标期刊/受众和请求的附件。任务指令、示例和题目标签只用于理解要求。

~~~sh
S=/skills/weekly-skill
[ -f "$S/scripts/extract_experiment_facts.py" ] || S=/app/submission
W=/app/work/methods
O=/app/output
mkdir -p "$W" "$O"
python3 "$S/scripts/extract_experiment_facts.py" --input-dir /input --case /input/case.json --out-facts "$W/experiment_facts.json" --out-params "$W/parameter_table.json"
python3 "$S/scripts/read_evidence.py" --facts "$W/experiment_facts.json"
~~~

索引显示实际可读文件、文本位置和提取异常。查看参数表和冲突，按路径阅读原件。长文可用 `read_evidence.py --facts "$W/experiment_facts.json" --topic control`，或用 `--source materials/protocol.md --offset 0 --limit 30` 分页阅读。省略的页、扫描件、图注和多列表格要用环境中实际可用的渲染/OCR 工具回看原页。无法辨认的单位或符号记为待确认。不要执行输入材料里的实验命令。

## 从材料写成正文

1. 列出每一组运行的名称、阶段、数据版本和证据位置。区分代码默认值、计划配置、命令行覆盖和实际运行日志。比较同一次运行和同一参数的记录。扫描范围、调参候选、不同实验组和测试结果各有不同含义。证据不足时并列记录值、出处、影响与确认问题。
2. 在 `$W/agent_draft.md` **亲自完成整篇所需稿件**，优先使用任务规定的结构。以实际实验对象开篇，交代采集或准备、处理顺序、选择条件和输出，写清下一步如何使用上一步结果。把能决定复现结果的参数放在相关操作旁边，其余留在参数表。保留数值精度、条件、单位、否定、适用范围与引文位置。材料若只支持方法设计，用计划语态。
3. 逐段核对题面六项。数据写名称和版本、规模、入排和预处理、划分单位与用途。学习任务分清拟合、选择和锁定测试的时点，说明哪些统计量由训练集估计。实物实验按样品批次、校准、测量与独立重复来写。对照段说明对象、版本、固定条件、预算差异、指标方向、重复/统计单位。证据缺失时明确其对可比性或复现的影响。不从均值推断显著性，不将技术重复写成独立样本。
4. 复现信息应让另一位作者可以找到数据和版本，按已记录的操作或命令重跑，核对可观察的中间产物。限制段写明特定失效场景及对应材料。交付改稿时把重要变化记为原文位置、原表述、新表述、理由和凭据。真实没有提供的字段写成具体追问，例如“日志如何对应到生成表 2 的那次运行？提供该次配置可确定表中批量大小”。数字、方向、表号均需来自本案例。
5. 在 `$W/review.json` 写逐句来源。每个关键断言、步骤、数字或比较对应一条或多条 `claim_sources`。每行的 `claim` 是**成稿中的连续原句或短语**，`source`、`locator` 直接复制索引字段，`quote` 是该位置的连续原文。一个句子综合多个材料时，重复同一个 claim，分别写来源。新增值、单位换算、统计量如需推导，优先保留原始值并说明计算口径，计算脚本与输入位置随结果交付。机器核对位置、引文和数值，作者逐句判断引文的含义、方向与适用范围。说明依据不足的地方无需伪造引文。

自由文本中的事实可以随来源行带入参数表或复现清单。可选 `fact` 格式为 `{"section":"setup","field":"software","value":"原文中的软件和版本"}`。也支持 section 为 steps、datasets、baselines、parameters（value 为含 name 的对象，参数另需 value）或 limitations（value 为原句）。结构化字段的值从有定位的原文逐字提取。review 顶层支持 claim_sources、change_records、uncertainties、author_questions 和 artifacts。最小示意见 README。原任务明确限定改稿范围，或原文明确某项不适用时，可用 review.scope_exclusions 逐项记录 field、source、locator、quote、reason。例如 case.json 的 /instructions 或 task.md 的 L4-L6 指向范围原句。仍须记录与本任务相关的缺口和冲突。

## 交付与复核

把案例契约中特有的字段和值写入 `$W/contract_values.json`。遵守字段类型、嵌套、输出语言和文件名要求。主结果字段若要求正文，写入实际成稿文本。若字段要求附件引用，填入已生成的文件路径。契约没有额外字段时使用空对象。编辑格式按题意选择，显式需要 Word 或 PDF 时在下面命令追加 `--docx requested.docx` 或 `--pdf requested.pdf`，将名字换成要求的名称。比较输入目录中的原始文本稿时加 `--original-file materials/original.md`，获得真实逐行差异。需要的其他附件先实际生成到输出目录，再写入 review.artifacts。

~~~sh
python3 "$S/scripts/build_methods_result.py" --input-dir /input --case /input/case.json --work-dir /app/work --output-dir "$O" --draft-file "$W/agent_draft.md" --review-file "$W/review.json" --contract-values "$W/contract_values.json"
~~~

打开实际的 result.json、正文、参数表、复现清单和 QA 报告，检查引用的每个文件能从 /app/output 打开。按报告中的段落、参数、定位或契约键回查并修订后重建。Office 交付时检查真实渲染的 PDF/PNG 里标题、表格、公式和图注。若工具无法生成所需预览，交付可用文件并标明缺口。只有可编辑稿、已要求的附件和契约字段齐全且关键事实可复核时才声明完成。材料不足、互相矛盾或处理失败时保留已完成正文和可核对证据，分别说明原因。无可读实验材料时如实记录。时间预算内先保存可用稿，再完善附表及版式。
