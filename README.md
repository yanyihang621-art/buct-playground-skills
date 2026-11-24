# BUCT Playground Skills (`buct-playground-skills`)

北京化工大学（BUCT）参加 **玻尔产品周赛（第一期：AI 文献阅读与学术写作）** 的开源 Agent Skill 集合。

每个 Skill 子目录均采用**完全自包含（self-contained）**设计：包含 `SKILL.md`、确定性 Python 工具脚本（`scripts/`）、输出 Schema（`schemas/`）、最小输入输出示例（`examples/`）及独立 `README.md`，同时兼容 **Harbor 评测容器契约**（`/skills/weekly-skill`、`/input/case.json`、`/app/work`、`/app/output/result.json`）与本地跨平台运行。

## 赛题与 Skill 目录规划

| 序号 | 赛道与赛题 | Challenge ID | 仓库子目录 | 状态 |
|---|---|---|---|---|
| 1 | 阅读01｜文献文档解析 | `product-arena-08-3027d350` | [`skills/reading-01-doc-parsing`](skills/reading-01-doc-parsing) | 已完成 |
| 2 | 阅读10｜文献总结与结构化笔记 | `product-arena-06-6d8cf72b` | [`skills/reading-10-structured-notes`](skills/reading-10-structured-notes) | 已完成 |
| 3 | 阅读14｜Nanopublication | `product-arena-09-0e8befd4` | [`skills/reading-14-nanopublication`](skills/reading-14-nanopublication) | 已完成 |
| 4 | 阅读15｜文献知识图谱构建 | `product-arena-10-760d1a0b` | [`skills/reading-15-knowledge-graph`](skills/reading-15-knowledge-graph) | 已完成 |
| 5 | 阅读03｜文献翻译 | `product-arena-01-5a9928c1` | [`skills/reading-03-translation`](skills/reading-03-translation) | 已完成 |
| 6 | 写作04｜参考文献与引用管理 | `product-arena-03-429cc14d` | [`skills/writing-04-references`](skills/writing-04-references) | 已完成 |
| 7 | 写作13｜LaTeX 写作与编译 | `product-arena-07-865de7e4` | [`skills/writing-13-latex`](skills/writing-13-latex) | 已完成 |
| 8 | 写作02｜学术语言润色 | `product-arena-02-7051d92d` | [`skills/writing-02-polishing`](skills/writing-02-polishing) | 已完成 |
| 9 | 写作08｜引言与相关工作写作 | `product-arena-04-4b82ad21` | [`skills/writing-08-intro-related-work`](skills/writing-08-intro-related-work) | 已完成 |
| 10 | 写作09｜方法与实验章节写作 | `product-arena-05-64faa4a2` | [`skills/writing-09-methods-experiments`](skills/writing-09-methods-experiments) | 已完成 |

## 通用设计原则

1. **确定性脚本增强**：用几何排版分析、三线表/跨页表重建、上下标与希腊符号保真、自适应 OCR 预处理与 Schema/引用校验脚本替代单纯 Prompt 猜测。
2. **证据可溯源**：关键结论均绑定 `pdf_page`、`printed_page`、`bbox` 坐标与 `block_id` / `table_id` / `eq_id`。
3. **诚实降级与边界标注**：对加密 PDF、无文字层扫描件、图片型表格、乱码字体与页码错乱显式记录 `status` 与 `uncertainties`，绝不伪造数据。
4. **Harbor 与提交规范合规**：全部为 UTF-8 LF 纯文本文件、无符号链接、无硬编码本地绝对路径、单 Skill 目录严格控制在 50 个文件 / 2 MB 上限以内。
