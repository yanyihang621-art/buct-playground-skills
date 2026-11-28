# Reading 14 校验器故障注入与 RDF 一致性实测记录

- 运行时间 (UTC)：`2026-11-28 12:58:35Z`
- 测试脚本：`scripts/self_test.py`
- 全部测试通过：`True`
- RDF 导出校验：`{"mode": "rdflib_parsed", "counts": {"nquads": 113, "trig": 113, "jsonld": 113}, "nq_regex_errors": 0, "consistent": true}`

| 测试项 / 注入故障 | 期望检出码 | 实测检出 | 实际捕获编码 |
|---|---|---|---|
| altered_quote | `unverified_quote` | PASS | `unverified_quote` |
| dropped_negation | `negation_dropped` | PASS | `negation_dropped` |
| altered_number | `number_not_in_evidence` | PASS | `number_not_in_evidence` |
| wrong_anchor | `anchor_relocated` | PASS | `anchor_relocated` |
| may_as_asserted | `hedge_mismatch` | PASS | `hedge_mismatch` |
| happy_closed_and_rdf | 契约零错 + RDF 跨格式一致 | PASS | `exact=4` |
| encrypted_pdf_fallback | 状态降级为 unable/partial | PASS | `status=unable` |
