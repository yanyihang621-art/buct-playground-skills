# 方法与实验章节写作

给论文作者与科研写作 Agent 使用。输入可以是自由文本、论文、原稿、实验配置、代码和日志。输出是一份由作者根据实际材料完成的可编辑稿，附参数与来源表、逐句证据、修改记录、复现清单和待确认项。入口为 SKILL.md。

## 最小可运行示例

examples 中的传感器校验案例和短稿均为教学示例。示例展示如何把“窗口为 5 个样本”分别连回设置值、单位与作用对象。该例有专属契约字段 data_release。下面从 Skill 根目录运行，输出到新建的临时目录。

~~~sh
T=$(mktemp -d)
mkdir -p "$T/input"
cp examples/example_case.json "$T/input/case.json"
python3 scripts/extract_experiment_facts.py --input-dir "$T/input" --out-facts "$T/facts.json" --out-params "$T/params.json"
python3 scripts/read_evidence.py --facts "$T/facts.json" --source case.json --query window
python3 scripts/build_methods_result.py --input-dir "$T/input" --work-dir "$T/work" --output-dir "$T/output" --draft-file examples/tutorial_annotated.md --contract-values examples/tutorial_contract.json
~~~

示例稿每句话末尾的 ⟦…⟧ 由真实材料索引生成。打开 output/result.json 的 manuscript、evidence 和 data_release，可看到已去除侧注的稿件和完整来源。examples/tutorial_draft.md 与 examples/tutorial_review.json 给出同一案例的手工引用形式。output/parameter_table.md 可逐行核对每条参数的取值和位置。示例的真实复现测试会打开全部产物，篡改一条定位并添加一句无来源的数字，检查是否能报告对应问题。

Harbor 入口 `scripts/harbor_methods.sh` 将取材和发布分成两个独立命令。`prepare` 建立证据索引，`publish` 读取工作区的 `methods/agent_draft.md` 和实际存在的两个可选 JSON 文件。可通过 `METHODS_INPUT_DIR`、`METHODS_WORK_DIR`、`METHODS_OUTPUT_DIR` 在 POSIX shell 中本地重放。发布后用 `result.json` 中的 `status` 和 `verification` 判断交付状态。直接调用 Python 时，默认的严格退出码可用于 CI，`--allow-partial` 则允许交出已经写入的部分结果。

## 实际案例

1. 按 SKILL.md 在 Harbor 中读取案例和契约，建立材料目录。read_evidence.py 支持按主题、路径或短语查看摘录。若文档为扫描件，核对原页并保留页码。找不到来源的参数不可写成已使用值。
2. 结合读者、所需语言和论文格式撰写 agent_draft.md。研究设计、样品与数据集、比较对象、评估指标均以原材料为准。校验器允许整合标题，关键数字和结论仍要逐项对回证据。
3. 根据 read_evidence.py 提供的 ID 就地标注关键断言，也可对照 examples/tutorial_review.json 填写 claim_sources。每条取证记录最小需要 claim、source、locator 和 quote。幻灯、PDF 或 Word 的技术预览不能替代可编辑原件。修订现有文本可传 --original-file，逐行差异作为独立附件返回。关于缺材料、适用范围与字段类型见 SKILL.md。
4. 使用 --contract-values 传入案例自定义字段。生成 Word 或 PDF 时分别传 --docx 和 --pdf；PDF 预览应由实际 Word 稿渲染。qa_report.json 中 violations 是发现的问题，review_items 包含需要人工语义复核的事项。无成稿时脚本可生成抽取提纲，标为 partial。
5. 维护词表、来源类型或规则后重跑下面的本地对照。对代码默认值和日志不一致的案例，核对双方原文并向作者询问对应运行标识。

## 依赖与边界

脚本使用 Python 3.10+。JSON、文本、CSV/TSV、Python/Shell 源码等使用标准库。PDF 使用 PyMuPDF 或环境提供的文本工具。DOCX 输入支持读取文档 XML。YAML 的完整语法需要 PyYAML。Office 导出和预览使用环境可用的文档写入器、Office 渲染器与 PDF 工具。以运行结果中的 ingestion_issues 和 uncertainties 核实实际可用能力。索引有页数、条数和文本长度限制；扫描图像、多栏表和长附件需回看原件，表头与单位尤其重要。

运行 `python3 scripts/self_test.py --out-dir /tmp/writing09-test --compare-baseline`。对照将同一份文本交给首版的全文子串规则和新版局部参数核验，分别测试原稿、互换数值和同一参数的第二个矛盾取值。测试报告输出实际开关结果。竞赛得分由平台使用固定案例独立计算。
