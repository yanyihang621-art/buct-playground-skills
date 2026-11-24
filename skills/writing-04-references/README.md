# writing-04-references (`写作04｜参考文献与引用管理`)

纯 Python（不依赖 `bibtexparser`）学术文献引用与参考文献管理 Skill，覆盖 **多格式文献库解析、可解释去重、论断级精准插引、顺序编码/著者-出版年重排与双向同步、6 项确定性引用质检**。

## 支持格式与规范

- **输入文献库**：BibTeX (`.bib`)、RIS (`.ris`)、EndNote Tagged / CNKI (`.enw`)、JSON、纯文本列表。
- **目标引用样式**：`GB/T 7714-2015`（含中英文混排 `等`/`et al`、`[J]`/`[C]`/`[M]`/`[D]`/`[EB/OL]`）、`APA 7th`、`IEEE`、`BibTeX`。
- **稿件格式**：Markdown (`.md`)、LaTeX (`.tex`)、纯文本 (`.txt`)。

## 本地自测

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
python skills/writing-04-references/scripts/self_test.py --out-dir ..\local_test_outputs\writing-04 --compare-baseline
```

## 6 项质检门控 (`qa_report.json`)

1. `numbering_continuity`：文内引用序号 `1..N` 严格连续无跳号。
2. `bidirectional_sync`：正文引用与文末参考文献 100% 双向匹配（零悬空、零冗余）。
3. `dedup_integrity`：规范库无残留重复 DOI 或同年份高相似重复条目，且防同名同作者不同论文误并。
4. `metadata_and_doi_validity`：检出缺失关键字段与伪造/语法错误 DOI（`10.0000/...` 等）。
5. `claim_reference_alignment`：正文引文句与所引文献标题/关键词/摘要主题一致性核查。
6. `placeholder_and_honesty`：禁止未解析 `[CITE:...]` / `[@...]` 与占位符残留，诚实标注 `offline_syntax_only` 与 `uncertainties`。
