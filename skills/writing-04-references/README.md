# 参考文献与引用管理

给出稿件和文献库，生成可编辑的引文修订稿、按目标体例排好的书目，以及逐句可复查的证据。运行入口为 `scripts/build_reference_result.py`。执行案例的 Agent 负责阅读证据、选择引用、判断论断的支持程度和完成案例特定交付。

## 在本地运行教学案例

以下命令使用 Python 3.10+。从本 Skill 根目录运行，示例完全使用合成教学材料。

```sh
mkdir -p demo/input
cp examples/example_case.json demo/input/case.json
python3 scripts/build_reference_result.py --draft --input-dir demo/input --work-dir demo/work --output-dir demo/work/draft
python3 scripts/build_reference_result.py --input-dir demo/input --work-dir demo/work --output-dir demo/output --review examples/example_review.json
```

旧编号 `[7]` 在改稿中成为首引 `[1]`。对照 `demo/work/draft/evidence/claim_evidence.json` 与最终目录中的同名文件。第一遍定位引文，第二遍将论断与教学摘要中的原句配对，记录理由并填入 `editor_note`。`--draft` 跳过 Word 渲染和 TeX 编译，最终运行再生成正式成品。输出节选见 `examples/example_result.json`。PowerShell 可用 `New-Item` 建目录、`Copy-Item` 复制文件和 `python` 运行。

## 输入与依赖

- 支持多层目录、内联 JSON 或显式路径。稿件可为 Markdown、TXT、TeX、PDF 或 DOCX。文献库包括 BibTeX、RIS、EndNote、CSL-JSON、CNKI 常见导出及带表头的 CSV/TSV。输出契约、自测示例与任务说明不作为稿件。
- 核心文本流程使用 Python 标准库。PDF 读图文层使用 PyMuPDF、Poppler 或 pypdf；扫描稿需结合环境 OCR 校读。生成 Word 时优先保留源 DOCX 的表格和 run 样式；完整预览依赖 LibreOffice 或 Windows 上的 Word。用 PyMuPDF 或 Poppler 导出首页图。
- 内置 GB/T 7714 顺序制、IEEE、APA 7 的常用字段映射以及 BibTeX。特殊期刊规定、叙述式引文、有定位页码的复杂引注、异体作者名和 Word 文献域需要逐项审阅。在线 Crossref 查验为显式选项，登记元数据与论断内容分别核查。

## 文件与复核

| 路径 | 如何使用 |
| --- | --- |
| `synced_manuscript.md` 或 `.tex`，可选 `.docx` | 检查正文、表注、附录、模板格式与文末表。Office 预览用于视觉检查 |
| `bibliography_*.txt` 或 `.bib`、`references_synced.bib` | 对照正文首次引用顺序或 APA 的作者排序，核查卷、期、页、DOI |
| `evidence/material_inventory.json`、`raw_library.json`、`canonical_library.json` | 核查材料角色、记录来源、旧号与规范键 |
| `evidence/dedup_report.json`、`citation_map.json` | 查合并依据、别名、被拒绝的歧义匹配、每处原标记及改后标记 |
| `evidence/claim_evidence.json`、`qa_report.json` | 按行号和引用点检查论断、原句、文献身份、证据级别和待确认项 |
| `evidence/final_claim_audit.json`、`retained_citations.json` | 处理已经定位的原文矛盾、外部定量结论漏引和保留旧稿时仍可核验的引文 |
| `evidence/manuscript.diff`、`change_log.json` | 逐项复查变更；同输入和同一份审阅表可以重跑 |
| `result.json` | 案例 ID、状态、指标、产物路径、来源、变更和原始输出契约中的字段 |

第一遍的 `partial` 可用来继续工作。找不到原编号、同一锚点出现数次或引用来源有冲突时，核对原件并在 review 中补足定位。材料不全、字段缺失、无法渲染和无法核实的结论分别记录。review 字段说明和容器调用见 `SKILL.md`。

## 复现测试

多文件 TeX 的输出包含可编辑工程、真实编译的 PDF（引擎可用时）和书目收敛报告。Word 的原生引用、Zotero 和 EndNote 域优先从字段指令和源库解析。可确认的引文生成可编辑静态稿，原动态域另存，保留脚注与尾注锚点。已有明确页码的作者—年份引文在转换时保留页码及归属。Word 原生域增强处理使用 lxml；其余 Word 处理和预览的依赖见上文。

`evidence/claim_coverage.json` 枚举正文分句、原句和引文位置，给出数值或方向待核事项。规则生成的候选需要阅读原文。对严格限制字段的案例，`result.json` 遵循案例指定的字段集合，完整审计另存于 `evidence/contract_audit.json`。`evidence/artifact_manifest.json` 记录每份已登记产物的相对路径、大小及 SHA-256。

```sh
python3 scripts/self_test.py --out-dir /tmp/writing04-self --compare-baseline
python3 scripts/regression_cases.py --help
python3 scripts/test_author_date_delivery.py
python3 scripts/test_claim_coverage.py
python3 scripts/test_final_claim_audit.py
python3 scripts/tex_project_test.py
python3 scripts/word_cases.py --out-dir /tmp/writing04-word
```

自测覆盖去重、连续编号、未解析引文、元数据问题、引文证据错配和逐字引文的真实性。版本对照工具读取同一组输入，分别调用两个版本的真实入口，再检查实际交付文件。公共样例只用于工程验证；正式的基础 Agent 对照、隐藏案例和分数由平台产生。维护时将新增样式或输入格式加入可重跑的测试。
