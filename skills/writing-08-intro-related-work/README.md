# writing-08-intro-related-work (`写作08｜引言与相关工作写作`)

面向学术论文“引言与相关工作”的可核验起草与审校 Skill，解决传统大模型写作中常见的 **空洞套话开篇、流水账式逐篇拼贴、无据推导研究空白、贡献与实验结果脱节** 问题。

## 核心产出与质检门控

- `comparison_matrix.md`：按技术路线分组的文献对比矩阵（问题设定 / 评价体系 / 核心结论 / 局限性）。
- `intro_and_related_work.md`：四要素齐备（研究背景 / 相关工作 / 研究空白 / 主要贡献）且引用与证据锚点可回溯的可编辑 Markdown 稿件。
- `qa_report.json`（6 项门控）：四段结构完整性、反套话与反流水账、空白文献可溯源性、贡献与证据锚点一致性、引用覆盖与元数据完整性、材料缺口诚实标注。

## 本地自测

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
python skills/writing-08-intro-related-work/scripts/self_test.py --out-dir ..\local_test_outputs\writing-08 --compare-baseline
```
