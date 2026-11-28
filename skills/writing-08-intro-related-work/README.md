# 写作 08 · 引言与相关工作写作 (`writing-08-intro-related-work`)

围绕真实科研材料（双栏 PDF、DOCX、BibTeX/RIS、JATS XML、JSON/JSONL、Markdown/LaTeX），撰写或修订结构严谨、横向可比、论断逐条溯源的学术论文引言与相关工作。

## 核心工作流

完整容器命令见 [SKILL.md](SKILL.md)：

1. `scripts/prepare.py`：采用双栏感知 PDF 解析内核与 OOXML/BibTeX/RIS 解析器摄取全部材料，生成阅读稿 `reader/*.md`、未截断 `case_brief.md`、`answer_template.json`、`ledger_template.json` 及保底骨架 `result.json`。
2. `scripts/find.py`：按书目（`--bib`）、表格（`--tables`）、章节、角色或全文正则检索定位原文。
3. Agent 亲自撰写 `/app/work/w08/draft.md` 及紧凑证据台账 `/app/work/w08/evidence_ledger.json`（以及按需填写 `/app/work/w08/answer.json`）。
4. `scripts/finalize.py`：渲染指定引文风格（数字制/作者-年份制/保留源键）、生成技术路线对比矩阵 `comparison_matrix.md`、执行任务感知 7 重质检（区分阻断性 `must_fix` 与建议性 `warnings`）、导出可编辑稿（MD / DOCX / LaTeX / PDF / PNG 预览），并严格对齐 `output_contract`（含封闭契约 `additionalProperties: false`）输出 `/app/output/result.json`。

## 本地验收命令

```bash
export PYTHONDONTWRITEBYTECODE=1
python3 -B scripts/self_test.py --out-dir ../local_w08_test
```

## 依赖与范围

- 材料解析、引文审计、7 重质检与契约打包基于 Python 3.10+ 标准库 + `PyYAML`。
- 双栏 PDF 解析与 PNG 预览使用 `PyMuPDF`（`fitz`），DOCX 导出优先使用 Pandoc，无 Pandoc 时自动回退至支持 Markdown 表格的 `python-docx` 原生写入器；PDF/PNG 预览支持 LibreOffice / Word COM / XeLaTeX / `pdftoppm` / PyMuPDF。
