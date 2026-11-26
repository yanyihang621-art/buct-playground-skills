# 文献总结与结构化笔记

把单篇或多篇论文变成与原章节对应的笔记、可比较的论文卡片和带出处的案例答案。可以在本地作为 Python 工具运行，也可由 Agent 根据 [SKILL.md](SKILL.md) 完成竞赛案例。

## 最小可运行示例

`examples/` 中的光学传感器短文、案例与复核稿均为合成教程。以下两次调用产生 [示例结果](examples/example_result.json) 以及包含原句与行号的 Markdown 笔记，不需要联网或创建凭据。

```bash
python3 scripts/run_case.py prepare --input examples --case examples/example_case.json --work ./work --out ./out
python3 scripts/run_case.py finish --input examples --case examples/example_case.json --work ./work --out ./out --review examples/example_review.json
```

运行自己材料时，将案例和论文放入输入目录。prepare 保存阶段性结果，生成 `work/reader/paper_*.md` 和 `work/ingested/ingest_manifest.json`。按照案例逐章阅读，检查实验表格与脚注，将答案、限制及引文写入复核稿。finish 验证输入未改变，使用复核稿生成最终交付。变更原文后重新 prepare。Harbor 直接用 `python3 /skills/weekly-skill/scripts/run_case.py prepare` 和 `finish`，默认输入在 `/input`，交付至 `/app/output`。

## 输入、定位与输出

| 输入 | 支持的来源与位置 |
|---|---|
| PDF | 正文、原始章节、表格、公式和图注，保留物理页、章节及区块位置。扫描与损坏文件会产生状态和诊断。 |
| Markdown / TXT | 标题、段落及 Markdown 表格。证据定位到原文件行，不把文本行冒充印刷页。 |
| JSON | 独立文章、论文合集、案例中明确的内嵌论文、清单引用。正文引用 JSON Pointer，保留作者、章节和原始段落 ID。 |
| JATS XML | 文章元数据、摘要、正文层级、段落和普通表格。以 XML 路径及存在的段落 ID 定位。 |

默认递归查找资料。案例提供 `input_files` 等材料清单时按所列文件处理，文件声明可以是路径字符串或含 `path` 的对象。对于同一论文的多个版本，按题意选择主版本，标注补充材料和异文。图片中的表格、公式、合并单元格和缺失字体宜打开对应原页确认。

`out/result.json` 遵循本案例契约，支持嵌套类型与闭合对象。完整的 `out/notes/` 包含结构化笔记、可编辑 Markdown、论文比较矩阵、检索索引、案例答案对应证据及审计报告。审计失败时，查看 `work/latest_audit.json`。成功完成后的再次复核如失败，将保留上次发布的结果。

`examples/example_review.json` 展示逐章摘要、卡片、自由契约字段与证据绑定。JSON Pointer 指向具体回答值；同一数值比较可引用表头、数行和必要脚注。 `schemas/field_dictionary.yaml` 定义论文卡片字段，`templates/paper_note_template.md` 提供独立复用的笔记模板。严格验证外层契约不会限制独立笔记文件的内容。

## 运行环境

建议 Python 3.10+。JSON/JATS/Markdown/TXT 的阅读器使用标准库。PDF 使用 PyMuPDF，复杂表格使用 pdfplumber，OCR 依赖 Pillow 和系统可执行的 Tesseract。需要 PDF 环境时可执行以下命令。可选 jsonschema 用于验证完整标准模式；缺少时检查常用嵌套类型、数组、枚举和字段限制。

```bash
python3 -m pip install PyMuPDF pdfplumber pypdf Pillow numpy PyYAML jsonschema
```

对于扫描件，OCR 语言需要系统中已有相应语言包。无法读取的材料必须列明影响范围和人工接管位置。没有明确报告某一条件时，应区分检索范围、可读性与原作者明确说明的局限。语义解释、作者推测、统计显著性和跨实验可比性仍需人工或 Agent 读原文判断。

## 验证与复用

```bash
python3 scripts/self_test.py --out-dir ./checks --compare-baseline
python3 scripts/test_review_workflow.py
python3 scripts/test_pdf_structure.py
```

自测含双栏论文、混合格式、16 页长文、扫描及加密文件、子目录和章节末尾的否定结果，以及改数值、改单位、换比较组与伪造引文的负例。测试使用本地合成材料。对照选项记录原始文本提取与结构化产物的工程差异；平台执行正式白板和 Skill 对照。PDF 标题、书签和首页边栏的独立回归见 scripts 中的 PDF 测试。问题反馈可在本仓库给出最小来源片段、输入契约、版本和失败的审计项，以便复现。
