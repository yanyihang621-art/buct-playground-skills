---
name: writing-08-intro-related-work
description: 撰写或修订学术论文的引言与相关工作（支持中英文）。按技术路线横向比较已有研究、从实证限制推导有界研究空白、将贡献一一对齐作者实验证据、核对引文格式与解释边界，并导出可编辑 Markdown/DOCX/LaTeX/PDF/PNG 预览。触发词：引言、相关工作、introduction、related work、research gap、文献综述。
---

# 引言与相关工作写作（writing-08-intro-related-work）

目标：**用 ≤ 18 次工具调用**完成文献与实验材料精读、高质量学术成稿撰写、证据对齐核验与交付。脚本负责双栏 PDF/DOCX/BibTeX 解析、引文格式转换、数值/单位/否定检查及多格式导出，**成稿正文必须由你亲自撰写或精修（严禁模板拼接）**。

## 五步执行流程

1. **解析材料并读取完整简报**（第 1 步即写保底骨架 `result.json`）
   ```bash
   export PYTHONDONTWRITEBYTECODE=1
   S=/skills/weekly-skill
   [ -f "$S/scripts/prepare.py" ] || S=/app/submission
   python3 "$S/scripts/prepare.py" --input /input --case /input/case.json --work /app/work/w08 --out /app/output
   ```
   - **必读完整文件**：通读 `/app/work/w08/case_brief.md`（未截断 `task.md`、任务模式、目标语言与引文风格、完整 `output_contract` 树、已索引文献表与材料缺口）及 `/app/work/w08/answer_template.json`。

2. **精读文献与作者研究记录**
   - 直接阅读 `/app/work/w08/reader/*.md`，或使用 `find.py` 定向检索：
     ```bash
     python3 "$S/scripts/find.py" --work /app/work/w08 --bib
     python3 "$S/scripts/find.py" --work /app/work/w08 --tables
     python3 "$S/scripts/find.py" --work /app/work/w08 "limitation|baseline|significant"
     python3 "$S/scripts/find.py" --work /app/work/w08 --page 3 --raw
     ```
   - 若遇扫描页或模糊图表，用 `--render-page N --out-png /app/work/w08/pN.png` 查看原页。

3. **撰写成稿与紧凑证据台账**（写入 `/app/work/w08/draft.md`，可选 `/app/work/w08/evidence_ledger.json`）
   - 按任务要求的语言、篇幅、结构（四节或连续段落）与引用风格撰写 `draft.md`；如为局部修改/修订任务，保留原文结构并只修订指定部分，在 `change_records` 记录改动与依据。
   - **立得住（Background）**：从具体科学问题、观测指标与现实约束切入，严禁“近年来受到广泛关注”等空话套话。
   - **分得清（Related Work）**：按方法路线/评测维度/问题设定分组横向比较，明确各方案适用条件、对照基准与局限；禁止跨不同数据集或指标强行排序，冲突结论须分条件并列说明；引用紧贴所支撑的论断（支持 `[@cite_key]`、`\cite{key}`、`[1]` 或作者-年份）。
   - **说得实（Research Gaps）**：明确指出已引文献组合后仍未解决的具体问题或边界；缺失全文属材料局限，不能当作创新依据。
   - **对得上（Contributions & Limits）**：每条贡献对应作者自身的方法、图表或实验结果，保留单位、样本范围与条件；若为观察性/相关性/单场景结果，在正文中紧邻结论如实写出解释边界。
   - 在 `evidence_ledger.json`（格式见 `/app/work/w08/ledger_template.json`）中补充/修正文献卡片 `paper_cards`、关键论断逐字短引 `claim_links`、`research_gaps`、`contributions`、`interpretation_limits` 与 `change_records`。若 `output_contract` 含专属结构化字段，写入 `/app/work/w08/answer.json`。

4. **确定性核验、导出与契约打包**（最多修正 2 轮）
   ```bash
   python3 "$S/scripts/finalize.py" --case /input/case.json --work /app/work/w08 --out /app/output
   ```
   - 若题目要求特定导出格式（如 DOCX/PDF/PNG），追加 `--format docx --format pdf --format png`。
   - 优先清零 `must_fix`（`empty_manuscript`、`unknown_inline_citation`、`unverified_quote`、`unresolved_placeholder`、`contract` 错误），并审阅 `warnings`（空话开场、单一摘要拼接、单位/数值/否定/情态偏差、缺少解释边界）。

5. **最终自查与预算保护**
   - 检查 `/app/output/result.json`、交付稿件、`comparison_matrix.md` 与预览图（如要求 DOCX/PDF 则检查实际渲染页）。
   - **预算护栏**：若工具调用接近 40 次或耗时超 600 秒，立即运行 `finalize.py` 交付有据 `partial` 结果，并在 `uncertainties` 写明缺口。
