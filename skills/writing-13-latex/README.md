# writing-13-latex (`写作13｜LaTeX 写作与编译`)

面向学术论文 LaTeX 源码诊断、确定性安全修复与多遍可复现编译 Skill，支持 `pdflatex` / `xelatex` / `lualatex` 与 `bibtex` / `biber`，并集成 PyMuPDF `??`/`[?]` 渲染巡检与 PNG 预览输出。

## 核心脚本

- `scripts/inspect_project.py`：识别主 `.tex`、`\input`/`\include` 依赖树、包清单、自动引擎判断（CJK/`fontspec` 自动切 `xelatex`）与缺失资源巡检。
- `scripts/parse_tex_log.py`：解析 `.log` 与 `.blg`，区分根因错误与级联报错，统计未定义引用与 `Overfull \hbox`。
- `scripts/lint_and_fix_tex.py`：检测环境/表格列数失配，并确定性安全修复数字后未转义 `%`、缺失宏包与 `hyperref` 顺序问题，输出 `patch.diff`。
- `scripts/compile_latex.py`：可复现多遍编译 + PDF SHA-256 + 150-DPI PNG 预览 + `build_repro.sh` 生成。
- `scripts/build_latex_result.py`：Harbor 端到端编排与 `/app/output/result.json` 生成。

## 本地真实 TeX 编译自测

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
python skills/writing-13-latex/scripts/self_test.py --out-dir ..\local_test_outputs\writing-13 --compare-baseline
```
