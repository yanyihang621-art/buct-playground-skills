---
name: writing-13-latex
description: "依据真实科研材料撰写或修订可编辑 LaTeX 稿件、迁移指定模板、定位编译根因并验证公式、表格和引文。Use for source-grounded LaTeX authoring, journal-template migration, build repair and reproducible delivery."
---

# LaTeX 写作与编译

交付读者能继续编辑、按记录重新编译的稿件。每个案例的具体写作任务、目标语言、模板和交付字段由该案例决定。编译成功只能回答工程能否构建，稿件是否完成还要逐项核对任务和原始材料。

## 读取案例并建立工作副本

先读 `/input/case.json`、随附 `task.md` 和实际材料，找出本次要改写的段落、须保真的数字和公式、指定模板与引擎、所有 `output_contract` 字段和文件名。把任务里的动词转成可检查的交付项，比如“按指定模板排为双栏，并在正文引用实验表”。在任意工作目录可这样定位工具：

```sh
SKILL_DIR="${SKILL_DIR:-/skills/weekly-skill}"
[ -f "$SKILL_DIR/scripts/prepare_case.py" ] || SKILL_DIR=/app/submission
TOOLS="$SKILL_DIR/scripts"
S=/app/work/latex-case
mkdir -p /app/work /app/output
python3 "$TOOLS/prepare_case.py" --input-dir /input --stage "$S"
```

读取 `$S/materials.json`。索引把 DOCX 的段、表单元格和 PDF 页与原文件相连，ZIP 内的成员带有归档定位符，显式内联文件保留 JSON Pointer。直接读取原始 CSV/BibTeX/TeX 与 case.json 中的科研事实。若 PDF 文本层空、公式或表格提取失真，渲染对应页核对，必要时 OCR。原稿快照在 `$S/baseline`，只修改 `$S/project`。重试时继续已有工作副本。对于编号相同的多个模板样例，要依据题意、正文内容及入口选择目标。

## 在素材上完成本次论文工作

1. **已有工程**。在工作副本运行 `inspect_project.py --project-dir "$S/project" --out "$S/map.json"`。多主文件时用 `--main-tex 相对路径` 指定。本次选定的工程入口记为 shell 变量 `MAIN`。保留指定文类、模板宏、作者命令、图表文件以及实际引用的子目录。参考原始源文件或用编译工具在独立工作副本重现错误，保存初始日志后再改。
2. **写作、迁移或从材料创建稿件**。亲自完成要求的段落、公式、图表、引用与模板必填项。若输入是片段、Word、PDF 或内联事实，在 `project` 下用指定模板建立入口和可编辑子文件。若未指定模板，可选与目标文体匹配的标准文类。按底稿逐项保留有效数字、符号、上下标、单位、样本量与限定语。跨页表用重复表头，双栏稿先选择可读列宽或跨栏浮动体。用提供的图和数据，补齐正文到公式、表、图及文献的真实引用。依据原文和 BibTeX 条目解决歧义，缺 DOI、作者或实验值时显式记缺。外部文献不足时只写有来源的内容。
3. **定位并修源码**。从编译日志中的首个独立根因找到源文件与行号，并检查同一根因引发的级联错误。确定性修复可用 `lint_and_fix_tex.py --project "$S/project" --main "$MAIN" --apply --out-log "$S/lint.json" --out-diff "$S/lint.diff"`。检查实际 diff。对剩余错误按源码改正，例如对齐列数与跨列单元格、检查数学环境和花括号、给同一对象的标签及引用同步改名、从实际图片目录恢复路径。先查模板已加载的包，保留作者宏和科研内容。遇字体缺失，以字体清单确认替换与字符覆盖，将模板允许的替代字体记入说明；强制指定的字体或文类缺失则记录限制。
4. **写下可核验依据**。在 `$S/report.json` 写对象。按下面字段保留实际证据，键名需与该案例的契约一致。对于只要求修复的稿件，可选关键未变的数值或公式佐证科学内容保真。
   - `source_mapping` 数组。每条给 `source`（materials.json 来源标签或 `case.json#/路径`）、`source_quote`（原文短引）、`target`（相对工程根的 .tex）、`target_quote`（实际 TeX 字符串）与解释。可另给 `pdf_quote` 和页码 `page` 核验渲染值。表格应选源材料中的组标与数值，给 `pdf_row` 数组（字符串按实际列序），例如 `["2.0","21.7"]`，可查出虽在同页却排错行的数值。正文、公式和表格有不同来源时分别定位。
   - `task_checks` 数组。对照实际要求写 `requirement`、`status`（passed / partial / failed）和 `evidence`（源文件、标签、目标页或命令结果）。打开 PDF 核对表头与最后一列、公式编号及断行、字体缺字、图注和模板位置，特别检查改动所在页。页数和文件哈希不能验证科学含义。
   - `change_records`、`uncertainties` 记录有意义的修改原因、证据冲突与尚缺材料。`contract_values` 仅填写原 `output_contract` 额外要求的实质产物或数据，按原字段名、类型、路径和语义交付。案例指定文件名时，在 `artifact_exports` 写“目标相对路径 → 已生成的输出相对路径”，如 `{"deliverables/revised.pdf":"paper.pdf","deliverables/editable":"latex_project"}`。自制变更表应先在工作项目中生成并验证，再从 `latex_project/…` 导出。给 `contract_values` 中约定的文件字段填写对应目标路径。

## 构建、核验、交付

```sh
python3 "$TOOLS/build_latex_result.py" --input-dir "$S/baseline" --case /input/case.json \
  --project-dir "$S/project" --main-tex "$MAIN" --report "$S/report.json" \
  --stage "$S" --material-dir /input --work-dir /app/work --output-dir /app/output --no-fix
```

需要指定引擎时追加 `--engine pdflatex`、`xelatex` 或 `lualatex`。查看最终 `result.json` 及 `evidence/delivery_record.json` 的 artifacts，阅读 `evidence/build_comparison.json`。若契约规定 `additionalProperties: false`，请在完整交付清单中查看扩展诊断。按报告的 JSON Pointer 修复缺少的嵌套字段、实际文件或类型不符，再重建。对照原稿与修订稿在本环境的实际入口、引擎版本、各遍退出码、引用、报警、页数和源码差异。没有起始 TeX 的案例仅记录本次可观察到的构建。即使命令退出非零，也先读已保存的诊断，依据具体证据回到工作副本修复。传入 stage、原始材料和 report 后，打包工具会生成 `/app/output/evidence_checks.json`。打开这份文件，检查可检索的原文、最终源码和 PDF 短引的位置，对手工核验项记录页码和可见内容。对 `ambiguous_numeric_percent`，比对底稿并修正被注释吞掉的文字；若确认是有意注释，使用 report 的 `confirmed_comments` 准确记录 `file`、`line` 和 `reason`。

对 `??`、未解析 cite、越界表格、缺字或逐条证据失败，修正后重新打包并检查。核对模板中仍可见的待填标题、作者、机构与正文插入提示，用底稿中的真实信息替换。匿名评审按任务要求保留匿名格式，材料未给出的作者资料应记录缺口。打开被标记的具体 PDF 页和 PNG。遇宽表时先定位实际不可见的列或读数；可按表意换行、转置、拆表、跨双栏或改用横页，保持原有列义、单位和全部有效数字。对改后表格逐行检查组别与数值，并复核裁切位置和最后一页。确认交付的项目可从干净目录复编，最终 JSON 有原 `case_id`、全部契约字段和真实状态。保留已完成部分，区分“缺输入”“未验证”“构建失败”，将具体文件与下一步写入结果。每案例预算 900 秒，先保存可编辑稿与现有证据，再改善排版。

可直接运行的最小案例和书写报告的示例见 [README.md](README.md)。
