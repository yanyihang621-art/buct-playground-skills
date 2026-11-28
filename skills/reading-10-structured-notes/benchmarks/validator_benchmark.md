# Reading 10 校验器故障注入与端到端实测记录

- 运行时间 (UTC)：`2026-11-28 13:31:56Z`
- 测试脚本：`scripts/self_test.py`
- 全部测试通过：`True`

| 测试项 / 注入故障 | 期望检出 / 验证目标 | 实测结果 | 观测细节 |
|---|---|---|---|
| fault:altered_quote | `unverified_quote` | PASS | `unverified_quote` |
| fault:dropped_negation | `negation_dropped` | PASS | `negation_dropped` |
| fault:altered_number | `number_not_in_evidence` | PASS | `number_not_in_evidence` |
| fault:unit_swap | `unit_mismatch` | PASS | `unit_mismatch` |
| fault:wrong_anchor | `anchor_relocated` | PASS | `anchor_relocated` |
| happy_closed_contract | 契约零错 + Frontmatter + 真实锚点 | PASS | `exact=25` |
| open_contract_no_clobber | 开放契约无覆盖 + 非包装 answer.json | PASS | `issues=0` |
| auto_projection_from_notes | 笔记自动投影封闭契约 | PASS | `issues=0` |
| multi_format_docx_jats_csv | DOCX/JATS/CSV 多格式与内联表 | PASS | `papers=3, tables=2` |
| cross_block_continuation | 跨页断词续接引文精确匹配 | PASS | `match=exact` |
| encrypted_pdf_fallback | 状态降级为 unable/partial | PASS | `status=unable` |
