# 阅读 03 · 文献翻译

本 Skill 面向英文论文的中文翻译。先解析赛题指定的文献形成段落索引，把译者撰写的译文按 ID 对齐，再生成双语 Markdown、HTML、纯中文稿、词表、来源和质检报告。完整的两步命令和最小译文输入示例见 [SKILL.md](SKILL.md)。

## 文件与运行环境

Harbor 使用镜像中已有的软件。本地复跑用 Python 3.13.9、PyMuPDF 1.27.2.3、Pillow 12.2.0、PyYAML 6.0.3 和 pdfplumber 0.11.10。在新虚拟环境中可运行 `python -m pip install PyMuPDF Pillow PyYAML pdfplumber`。扫描文字识别还需要系统中的 Tesseract 和相应语言包；未安装时可使用图片定位进行复核。

`scripts/build_translation_result.py` 是准备和交付入口。PDF 文字提取使用 PyMuPDF，表格和扫描页可使用当前环境已有的 pdfplumber、OCR 工具；文字缺失和无法核实的区域记录在 `uncertainties`。默认种子术语和结构定义见 `schemas/`。题面给定的术语优先，也可在译稿的 `glossary_additions` 中补充能从材料核实的新术语。研究对象不同且含义不同的缩写应在对应段落分别解释。

原文、译文、来源坐标和译法依据保存在 `translation/bilingual_doc.json`。缺少译文时保留原段并标记待译。公式、作者和参考文献等可按角色原样保留。PDF 页码、文本行、DOCX 段落及表格字段见各段的 `source_locator`。质检逐段返回问题及相关编号，检查数字增删、保护文本、术语漂移、分句极性、漏译和正文照抄。多句重组、术语歧义、指代与因果关系需结合原句复核。

## 可复跑示例

将 [示例案例](examples/example_case.json) 复制到独立输入目录并命名为 `case.json`。用 [SKILL.md](SKILL.md) 中的英文样句对应的译文 JSON 执行两步命令，路径替换为此次输入、临时和输出目录。[示例结果节选](examples/example_result.json) 记录该输入的实际输出字段。译文中的 12 Hz 和“仅”可逐项对应原句。

## 长文与指定章节

在新输出目录执行准备命令后即可看到有来源定位的双语初稿及待译段。长文可以按段号分批翻译，把每批已译内容单独保存，再用同一目录重建当前译稿。若案例只允许指定的顶层字段，`result.json` 按其要求交付，完整检查记录保存在 `translation/delivery_details.json`。

可用公开的 BERT 论文复跑摘要范围检查。下载原 PDF，将 `target_file` 指向它并设置 `translation_scope.sections` 为 `["Abstract"]`。在 2019 年 NAACL 版中，应选中物理第 1 页标题和两段摘要，在 Introduction 前结束。按原文核对引文、百分号和两组 SQuAD F1 指标。不同 PDF 版本的版式和段号可能变化。[论文原版](https://aclanthology.org/N19-1423/)

## 合成回归

从 Skill 目录运行以下命令。自测依赖 Python、PyMuPDF 和 Pillow。输出目录放在提交包之外。

```bash
python3 -B scripts/self_test.py --out-dir ../reading03-test-output --compare-baseline
```

自测覆盖可编辑译稿、跨页论文、表格、加密和扫描 PDF，另有漏译、正文照抄、分句否定错位、凭空添加数值及推测变确定等负例。对照项在同一合成输入上实际运行“原文直接复制”与“已审阅译稿”两组处理，用于检验质量门控。官方比赛成绩取决于平台在固定科研案例上的复评。

同一命令也会在 `t5/` 生成一份三句、两行表格的海岸观测样例与五份译稿。打开 `t5/reviewed/result.json` 可逐句核对段内字符范围、北侧和南侧各自对应的位移，以及表格的行列坐标。`omitted` 删除末句，`swapped` 对调两句的位移，`table-gap` 漏译最后一格。三组异常的输出各自标出问题段或单元格。`joined` 用 `sentence_targets` 把三个来源句对应到连成一句的三个中文分句。七项检查汇总在 `self_test_summary.json` 的 `sentence_and_table_regressions`。字面与数值检查仍可能放过同一句内的主宾颠倒，终稿需要结合原句审读。
