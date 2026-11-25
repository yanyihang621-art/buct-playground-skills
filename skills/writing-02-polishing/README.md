# 学术语言润色

给科研作者一份可编辑的完整改稿，以及能定位到原稿的逐句修改记录。编辑者处理语法、术语、语气和信息重心。脚本负责抽取原文、按原位置应用修改、比对事实和生成交付件。具体稿件的工作流程见同目录 `SKILL.md`。

## 运行教学示例

需要 Python 3.10 或更高版本。以下命令从本 Skill 根目录运行。示例是两句关于测量可重复性的合成摘要，第一句有主谓一致错误，第二句应保留对精密度影响的限定和引文。

```sh
python3 scripts/build_polish_result.py --input-dir examples --case examples/example_case.json --work-dir work --output-dir output --prepare-only
python3 scripts/build_polish_result.py --input-dir examples --case examples/example_case.json --work-dir work --output-dir output --edits-json examples/example_edits.json
```

打开 `output/revision_report.md` 对照原句与改句。“measurements was”改为“measurements were”，采样频率仍为 8 Hz。第二句的 “may” 在中文对照中对应“可能”，引文 [2] 原位保留。`output/result.json` 含案例标识、修改记录、产物路径及题目要求的其他字段。`output/patch.diff` 可逐行查看文本变化。`examples/example_result.json` 是示例节选。

## 适配其他稿件

- 在新目录放原稿、实际材料和 `case.json`。可用 `manuscript_file` 指定相对路径，多个章节用 `manuscript_files` 指定顺序。正文也可内联于 `draft_text`、`paragraphs` 等字段。若存在多稿，按作者要求用可重复的 `--source` 明确选择。用 `register` 指定摘要、论文、基金或审稿回复等语域。将目标期刊、语种、受众和范围写入任务说明。
- 优先采用作者附的术语表和风格指南。术语表支持 JSON 的 `custom_glossary` 映射，也支持有 `term,preferred` 列的 CSV/TSV。中英对译用两种语言的词对。在原文中查证缩写释义；词义有冲突时保留原文并标出位置。风格配置在 `schemas/style_profiles.yaml`，修改后用跨学科样例复核。
- 普通文本、Markdown、LaTeX 和 JSON 输入使用 Python 标准库。PyYAML 可读取完整风格配置。PDF 优先用 PyMuPDF，备选 pypdf 或 pdftotext。扫描页需根据原页核对或 OCR。已有 Word 文件用 lxml 保留 OOXML 结构，创建新 Word 文件需 python-docx。PDF 预览需要 LibreOffice，Windows 也可使用已安装的 Word 和 pywin32。每个案例使用独立工作目录和输出目录。
- 保留数字、单位、公式、引文、比较方向、否定和限定强度。审核时还要逐项核对实验组与数值的对应关系、主语和作用范围。双语交付尤其要核对单位译名和术语义项。公式域或受保护对象无法编辑、预览失败或缺稿时，查看 `uncertainties` 中的具体位置和原因。
- `schemas/polish_result_schema.json` 描述完整审校结果。当案例指定封闭输出格式时，`result.json` 采用案例字段，`polish_audit.json` 保存完整审校记录。对专有交付字段，提供 `contract_values` 映射或传入 `--contract-json` 文件。

## 验证

```sh
python3 scripts/self_test.py --out-dir work/local-regression --compare-baseline
python3 scripts/review_flow_test.py --out-dir work/editor-regression
```

第一组在同一份合成原文上实测原样保留与本地规则示范，检查英文、中文、LaTeX、术语冲突以及篡改保护项的反例。第二组走实际编辑接口，检查漏审、变更回退、双语数据、Word 表格和契约字段。正式案例的增益由平台使用同输入的基础 Agent 对照评测。
