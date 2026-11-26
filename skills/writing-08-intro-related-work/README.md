# 引言与相关工作写作

适用于有文献、作者手稿或实验材料，需要撰写或修改中英文引言、组织相关工作、核对引用及贡献边界的论文作者。Harbor 的执行入口是 `SKILL.md`。运行者负责阅读、比较和撰稿，脚本负责抽取材料与核查交付。

## 从材料到交付

Harbor 中可以从任意工作目录运行 `scripts/manuscript_workbench.py prepare`。按 `read --find 词语` 或 `read --source 相对文件名` 打开原文片段，将本案例的 E 编号写在相应论断后的证据注释中。一次写完稿件后运行 `finish --draft 稿件.md`。有案例专属字段或补充书目卡时加 `--notes 说明.json`。完整命令与注释示例见 `SKILL.md`。下面的独立脚本也支持已有的稿件与 JSON 编辑记录。

1. 在任意工作目录用 Python 3.10+ 执行 `scripts/ingest_materials.py`。它递归读取案例，按文件路径和原文位置建立索引。PDF 保留页码，DOCX 保留段落与表格行，JSON 保留字段路径。可用 `--index INDEX --find 关键词 --limit 6` 查找原文，或用 `--source S0001` 回查单条记录。编号仅在同一份索引中有效。
2. 按官方任务设定语言、读者、比较维度和交付格式。在工作目录写成稿和分析卡。用相同评价条件比较研究路线，分别记录被引文献的限制和作者自身的证据。数据不可比时说明差异。
3. 使用 `build_intro_rw_result.py`，传入 `--draft-file`、`--analysis` 和 `--materials-file`。运行方法见 `SKILL.md`。有自定义合同字段时在 `contract_fields` 按原结构提供真实内容，命名输出用 `artifact_names`。如需显式导出，可重复传 `--format docx|tex|pdf|png`。
4. 在输出目录打开可编辑稿、`comparison_matrix.md`、`claim_evidence.json`、`gap_contribution_map.json`、`qa_report.json` 和 `result.json`。每条证据卡给出正文、短摘录与原文件定位。先看阻断交付的错误，再逐条回到原文判断警告中的范围、语义与新颖性表述。重新打包前核对修订后的正文与卡片。

## 依赖和使用范围

核心输入与 JSON/Markdown 输出使用 Python 标准库。PDF 文本层抽取依次尝试 PyMuPDF、pypdf 和 Poppler 的 pdftotext。DOCX 可直接读取 OOXML。PDF 图像页需结合容器可用的页面渲染与 OCR 工具核对。表格跨页、图中小字、跨语种转述和统计条件均须复查原页。引用核查支持带 key 的 Markdown/LaTeX、带参考文献表的编号引用及可消歧的作者年份引用。重名与同年多篇需要人工或 Agent 根据元数据消歧。

Markdown 可直接编辑。交付稿与正文证据摘录共用同一套引文编号或作者年份格式。案例要求保留引用键时可交付源格式。按需用 Pandoc 导出 Office 或 TeX，实际 Word 稿预览使用 LibreOffice 或本机可用的 Microsoft Word。直接渲染 PDF 需要 XeLaTeX 与字体，PNG 可用 pdftoppm 或 PyMuPDF。返回码 0 表示完成，1 表示部分交付，2 表示调用或文件错误。准备新案例时使用单独的工作和输出目录。

## 可复现的合成示例

从本 Skill 根目录执行。运行结果写入指定的临时目录。

```sh
python3 examples/worked_example.py --out-dir /tmp/intro-rw-demo
python3 scripts/self_test.py --out-dir /tmp/intro-rw-check --compare-baseline
```

第一个示例使用两份传感器研究记录和一份现场采样协议，演示如何把两种室内测量设置归纳为有边界的空白，把作者贡献限定为已执行的采样设计。输入合同见 `examples/example_case.json`，示意输出见 `examples/example_result.json`，卡片写法见 `examples/editorial_notes.md`。可在全新目录追加 `--mixed-documents`，用 PyMuPDF 和 python-docx 生成真实 PDF 与带表格的 DOCX 再走同一套流程。

第二个命令覆盖中英文材料、原文校验、稿件质量和资料不足的反馈。对照使用相同合成材料实跑，可以检查缺陷是否被发现。官方得分由平台在固定案例上复评。
