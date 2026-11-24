# writing-02-polishing (`写作02｜学术语言润色`)

支持英文与中文学术文稿的 **可核验最小编辑润色 Skill**：覆盖语法与中式表达修正、多文体语域适配（期刊论文 / 学位论文 / 基金申请书 / 审稿回复 / 摘要）、数值/公式/引文不变量守恒、限定强度与因果相关性守恒、以及逐句原文/改文/修改理由/Diff 清单交付。

## 本地自测

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
python skills/writing-02-polishing/scripts/self_test.py --out-dir ..\local_test_outputs\writing-02 --compare-baseline
```
