# LaTeX 写作与可复现编译

这个 Skill 处理学术稿件的实际写作、修订、排版和错误诊断。入口是 `SKILL.md`。原稿、项目、文献和模板可分别位于子目录、ZIP 或 case.json 中。最终交付包括可继续编辑的源码、成功编译的 PDF、渲染预览和能够说明修改依据的报告。缺文件或编译失败时仍交付已完成的源码和诊断。

## 最小调用示例

仓库中的小型合成示例含两行测量数据、一份双文件 LaTeX 稿件和一张带编号的表。下列命令从 Skill 目录运行，要求 Python 3.9+、pdfLaTeX、booktabs 以及 PyMuPDF 或 Poppler。真实案例按需使用 XeLaTeX/LuaLaTeX、BibTeX/Biber 和模板依赖。Python 脚本的 `--help` 列出可调参数。

```sh
K="$PWD"
S=$(mktemp -d)
python3 "$K/scripts/prepare_case.py" --input-dir "$K/examples/project" --stage "$S/case"
python3 "$K/scripts/build_latex_result.py" --input-dir "$S/case/baseline" \
  --case "$K/examples/example_case.json" --project-dir "$S/case/project" --main-tex main.tex \
  --report "$K/examples/example_report.json" --stage "$S/case" --material-dir "$K/examples/project" --work-dir "$S/work" --output-dir "$S/out"
python3 "$K/scripts/verify_manuscript.py" --input-dir "$K/examples/project" --stage "$S/case" \
  --project-dir "$S/out/latex_project" --report "$K/examples/example_report.json" \
  --pdf "$S/out/main.pdf" --out "$S/out/evidence_checks.json"
```

打开 `$S/out/main.pdf`，核对流量 1.0 与 2.0 L/min 分别对应 12.4 与 21.7 Pa，正文表号应指向这一张表。示意结果见 `examples/example_result.json`。修改示例报告的一处数字再运行核验，可以看到哪一对来源和输出失去对应。

## 工具分工与复用

- `prepare_case.py` 保留原始材料快照、可编辑工作副本与带来源的文本索引。PDF 提取失败时核对原页；DOCX 文本提取覆盖正文段落和表格，公式、图形和修订需回看原文。
- `inspect_project.py` 识别入口、引擎与依赖。`compile_latex.py` 运行真实编译、生成逐遍清单和预览。`parse_tex_log.py` 可单独解析已有日志。对非默认模板，先保留原指定文类、字体、文献样式和版本。
- `lint_and_fix_tex.py` 可独立预检。需要修改符号、数据、版式或引文含义时，由作者结合原材料处理。复查最终补丁能看到人工和自动编辑的实际范围。
- `build_latex_result.py` 读入当前稿件、原稿、契约和作者报告并打包。项目根、入口、引擎、报告路径均可显式传入。`verify_manuscript.py` 在实际来源、稿件和渲染文本中定位报告列出的短引；对示意图、数学语义和表格的视觉对应须另行检查。
- `self_test.py --out-dir <单独的测试目录> --compare-baseline` 可重现含文献、源码错误、Unicode 引擎和缺失文类的测试。比较结果仅描述本机确定性流程，正式平台对照由组织方运行。

处理用户自己的稿件时，按实际任务填写 `report.json`，给关键数据、公式和引文添加来源。发现新模板问题时，以最小源码、首个独立报错、使用的发行版和期望输出复现，再运行自测与干净目录重编。
