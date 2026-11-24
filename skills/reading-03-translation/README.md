# reading-03-translation (`阅读03｜文献翻译`)

面向学术论文英译中场景的可核验双语翻译 Skill，覆盖 **术语锁定、整篇按阅读顺序分段、公式/引文/单位占位保护、双语对照与纯译文多视图输出、确定性质量门控**。

## 目录结构

```text
skills/reading-03-translation/
├── SKILL.md                     # Agent 执行协议与 Harbor 运行说明
├── README.md                    # 人类可读说明与自测入口
├── GITHUB_URL.txt               # 公开仓库地址声明
├── schemas/
│   ├── terminology_seed.yaml    # 学科双语种子术语表、标题映射与否定词对齐规则
│   ├── glossary_schema.json     # 锁定术语表 Schema
│   └── translation_schema.json  # 双语翻译结构化输出 Schema
├── examples/
│   ├── example_case.json        # 示例输入 case.json
│   └── example_result.json      # 合成示例输出（带 _example_notice 声明）
└── scripts/
    ├── ingest_source.py         # PDF / Markdown / TXT 统一解析入库
    ├── extract_terms.py         # 术语与缩写抽取 + custom_glossary 锁定
    ├── segment_and_protect.py   # 阅读顺序分段与公式/引文/图表号/数值单位保护
    ├── apply_translation.py     # 术语锁定译文组装与 JSON / MD / HTML 渲染
    ├── verify_translation.py    # 6 项确定性质量门控核验
    ├── build_translation_result.py # Harbor 端到端编排与 result.json 生成
    └── self_test.py             # 4 套合成夹具 + 4 项负向故障注入 + 基线对比
```

## 本地自测

```bash
PYTHONDONTWRITEBYTECODE=1 python scripts/self_test.py --out-dir ./local_test_outputs/reading-03 --compare-baseline
```

PowerShell 示例：

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
python skills/reading-03-translation/scripts/self_test.py --out-dir ..\local_test_outputs\reading-03 --compare-baseline
```

## 6 项确定性质量门控 (`qa_report.json`)

1. `status_honesty`：加密/损坏文档必须 `refused`；纯扫描件或未提取图片表格必须 `partial`。
2. `alignment_completeness`：译文与原文 `segment_id` 一一对应且无空段、漏段、重段。
3. `protected_token_fidelity`：公式、引用编号、图表号、数值与物理单位在译文中数量与内容 100% 保留。
4. `terminology_consistency`：全文所有锁定术语跨标题/摘要/正文/图注/结论统一译出，无同义词漂移。
5. `negation_and_scope_preservation`：源文否定词与限定词在译文中保留对应极性与范围标记。
6. `placeholder_and_leakage_check`：禁止残留未回填占位符、`TODO`/`TBD` 或大段未译英文泄露。
