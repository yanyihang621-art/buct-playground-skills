# Reading 10 校验器故障注入实测记录

- 运行时间 (UTC)：`2026-11-28 12:58:34Z`
- 测试脚本：`scripts/self_test.py`
- 全部测试通过：`True`

| 测试项 / 注入故障 | 期望检出码 | 实测检出 | 实际捕获编码 |
|---|---|---|---|
| altered_quote | `unverified_quote` | PASS | `unverified_quote` |
| dropped_negation | `negation_dropped` | PASS | `negation_dropped` |
| altered_number | `number_not_in_evidence` | PASS | `number_not_in_evidence` |
| wrong_anchor | `anchor_relocated` | PASS | `anchor_relocated` |
| happy_closed_contract | 契约零错 + 0 unverified | PASS | `exact=22` |
| encrypted_pdf_fallback | 状态降级为 unable/partial | PASS | `status=unable` |
