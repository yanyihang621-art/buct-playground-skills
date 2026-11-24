# reading-01-doc-parsing — 科研文献 PDF 文档解析 Skill

面向 **玻尔产品周赛 · 北化分站赛（第一期）｜阅读01：文献文档解析** 的工程化 Agent Skill。通过确定性 Python 解析脚本 + 显式 Schema 契约 + 异常诚实降级机制，系统性解决科研 PDF 解析中的四大核心问题：**提得全、排得对、拆得清、核得回**。

---

## 1. 模块架构

```text
reading-01-doc-parsing/
├── SKILL.md                              # Harbor / Agent 运行主指引
├── GITHUB_URL.txt                        # 公开 GitHub 仓库位置声明
├── README.md                             # 完整文档、依赖、示例与实测记录
├── schemas/
│   └── parsed_document.schema.json       # document.json 输出 JSON Schema
├── examples/
│   ├── example_case.json                 # 最小输入样例（标注仅作示例）
│   └── example_result.json               # 最小输出样例（标注仅作示例）
└── scripts/
    ├── __init__.py                       # 包版本信息
    ├── pdf_utils.py                      # 能力探测、几何计算、连字/Symbol映射、去连字符、原子写
    ├── diagnose_pdf.py                   # 预检诊断（加密/损坏/扫描/乱码/页码错乱映射）
    ├── extract_formulas.py               # 上下标 _{}/^{} 重建、Symbol希腊字母解码与公式块识别
    ├── extract_tables.py                 # 网格表 + 三线表(booktabs)多级表头 + 跨页合并 + 图片表检测
    ├── ocr_pipeline.py                   # 300 DPI 渲染、灰度/对比度/偏斜校正与 Tesseract TSV 坐标回映
    ├── layout_order.py                   # 递归 XY-Cut 多栏排序、页眉页脚/边注/脚注隔离、跨页续接
    ├── structure_parser.py               # 标题/作者/摘要、多级章节树、图表配对与参考文献列表
    ├── parse_pdf.py                      # 主调度 CLI（产出 document.json / full_text.md / tables 等）
    ├── query_parsed.py                   # 面向长文档的按节/页/表/检索低 Token 切片查询 CLI
    ├── build_result.py                   # 对齐 /input/case.json output_contract 的 result.json 构造器
    ├── validate_output.py                # 确定性验收审计器（契约、状态一致性、溯源覆盖率、pypdf 召回交叉校验）
    └── self_test.py                      # 5 类合成 PDF 回归测试与裸抽取基线对照脚本
```

---

## 2. 依赖与环境兼容性

- **Python**: 3.10+（推荐 3.11+）
- **核心库**: `PyMuPDF` (`fitz`)、`pdfplumber`、`pypdf`、`Pillow` (`PIL`)、`numpy`
- **可选系统工具**: `tesseract`（支持 `chi_sim+eng` 或 `eng`）、`pdftotext`、`pdftoppm`、`pdfinfo`
- **优雅降级**:
  - 若运行容器未安装 `tesseract` / `pytesseract`，`ocr_pipeline.py` 仍会完成 300 DPI 图像渲染与预处理保存至 `crops/`，同时在 `uncertainties` 中诚实标注 `ocr_unavailable` 并绝不伪造文本。
  - 所有脚本均通过 `Path(__file__).resolve().parent` 解析同级模块，无任何硬编码盘符或个人路径，兼容 Harbor `/skills/weekly-skill` 与本地执行。

---

## 3. CLI 快速调用

### 3.1 解析 PDF
```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/parse_pdf.py \
  --pdf /input/paper.pdf \
  --out /app/work/parsed \
  --ocr auto \
  --crops
```

### 3.2 按需切片查询（节省模型上下文预算）
```bash
python3 scripts/query_parsed.py --parsed /app/work/parsed/document.json --mode summary
python3 scripts/query_parsed.py --parsed /app/work/parsed/document.json --mode sections
python3 scripts/query_parsed.py --parsed /app/work/parsed/document.json --mode section --id sec_1
python3 scripts/query_parsed.py --parsed /app/work/parsed/document.json --mode tables
python3 scripts/query_parsed.py --parsed /app/work/parsed/document.json --mode search --query "CO2"
```

### 3.3 构造并验证最终交付 `/app/output/result.json`
```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/build_result.py \
  --case /input/case.json \
  --parsed /app/work/parsed/document.json \
  --markdown /app/work/parsed/full_text.md \
  --out /app/output/result.json

PYTHONDONTWRITEBYTECODE=1 python3 scripts/validate_output.py \
  --case /input/case.json \
  --result /app/output/result.json \
  --parsed /app/work/parsed/document.json \
  --pdf /input/paper.pdf \
  --out /app/work/validation_report.json
```

---

## 4. 异常处理与边界策略

| 异常情形 | 诊断码 | 系统处理与输出状态 |
|---|---|---|
| 密码加密 / 损坏 PDF | `encrypted_pdf` / `corrupt_pdf` | `status: "refused"`，不尝试暴力破解或编造文本 |
| 无文字层扫描件 | `scanned_no_text` | 自动 300 DPI 渲染、去偏斜与 OCR；若无 OCR 引擎则设为 `unverifiable`/`partial` 并输出页面 PNG |
| 字体映射乱码 (`\uFFFD` / PUA / CID) | `garbled_font_cid` | 保留诊断与原文警告，触发 OCR 回退，`status: "partial"` |
| 图片型表格 | `image_based_table` | 裁剪 300 DPI 区域图至 `crops/`，记录 `is_image_table: true` 与 `status: "partial"` |
| 印刷页码错乱/重复 | `disordered_page_numbers` | 溯源字段同时保留物理 `pdf_page` 与印刷 `printed_page`，记录 `partial` 提示 |
| 复杂多行二维公式 | `complex_2d_formula` | 输出 `crops/eq_*.png` 供视觉核验并标注中等不确定性 |

---

## 5. 本地可复现自测与基线对照

> 以下结果为运行本目录自带合成 PDF 测试集（`scripts/self_test.py`）的实际观测输出，**仅用于本地工程验证与回归测试，不代表任何官方赛题或榜单分数**。

复现命令：
```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/self_test.py --out-dir /tmp/reading01_test --compare-baseline
```

本机实测通过情况（`all_passed: true`）：

| 测试夹具（合成 PDF） | 验证内容 | 实测结果 |
|---|---|---|
| `t1_two_column_paper.pdf` | 双栏 XY-Cut 阅读顺序、页眉/页脚/边注/脚注隔离、连字符修复、`H_{2}O`/`Fe^{3+}`/`10^{-3}` 上下标、Symbol 字体公式 `ΔG = ΔH - T ΔS (1)`、图题与 3 条参考文献、Mock Harbor 契约全流程 | `pass: true`，`status: "success"`，20 个区块，溯源覆盖率 `1.0`（41/41），独立 `pypdf` 词元召回率 `0.9643` |
| `t2_complex_tables.pdf` | 三线表（`booktabs` + `cmidrule` 多级表头 `Metrics -> Precision/Recall/F1` + 表注）、跨页续表合并去重表头、`Table 3` 图片型表格裁剪检测 | `pass: true`，3 张表全部检出，`Table 2` 跨页 `[1, 2]` 合并为 4 条数据记录，`Table 3` 标记 `is_image_table: true` |
| `t3a_encrypted.pdf` | AES-256 加密 PDF 拒答与诚实状态 | `pass: true`，`status: "refused"` |
| `t3b_scanned.pdf` | 纯图像无文字层扫描页识别与诚实降级（本机无 Tesseract 环境） | `pass: true`，`page_type: "scanned_no_text"`，`status: "unverifiable"` |
| `t3c_garbled_and_disordered.pdf` | `\uFFFD`/PUA/`(cid:*)` 乱码字体 + 乱序页码（`Page 3, 5, 4`）检测 | `pass: true`，检出 `["disordered_page_numbers", "garbled_font_cid"]`，`status: "partial"` |

与原始单流提取（`fitz.get_text("text")` 基线）在同一合成夹具上的定性/定量对照：
- **页眉/页脚混入正文**：基线 `2` 处泄漏 → Skill `0` 处泄漏；
- **多栏与跨页顺序**：Skill 正确保持左栏 → 右栏 → 次页顺序及跨页段落续接；
- **化学/数学上下标结构化**：基线 `false` → Skill `true`（保留 `H_{2}O`、`Fe^{3+}`、`10^{-3}`）；
- **区块坐标溯源**：基线 `0` 个 `bbox` → Skill `20` 个带 `pdf_page`/`printed_page`/`bbox` 的区块；
- **复杂表格重建**：基线 `0` 个结构化表 → Skill 产出 3 个结构化表（含 `colspan="3"` HTML、跨页合并记录与图片表裁剪）。

---

## 6. 已知限制

1. 无边框且无水平三线的纯空白对齐表格目前不作猜测性拆表，以避免将多栏正文误拆成表格。
2. 当环境未安装 `tesseract` 且遇纯扫描件时，脚本仅产出高分辨率 PNG 图像与 `unverifiable` 状态，需要支持视觉的多模态 Agent 结合 `crops/` 图像完成读数。
3. 复杂多行分式/矩阵公式在 `extract_formulas.py` 中会保留单行拼接文本并触发 `complex_2d_formula` 裁图提示，需人工或视觉模型二次复核。
