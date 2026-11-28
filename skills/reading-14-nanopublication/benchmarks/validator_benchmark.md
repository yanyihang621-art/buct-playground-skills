# Reading 14 校验器故障注入与 RDF 四图一致性实测记录

- 运行时间 (UTC)：`2026-11-28 13:31:57Z`
- 测试脚本：`scripts/self_test.py`
- 全部测试通过：`True`
- RDF 导出校验：`{"mode": "rdflib_parsed", "counts": {"nquads": 129, "trig": 129, "jsonld": 129}, "nq_regex_errors": 0, "consistent": true}`

| 测试项 / 注入故障 | 期望检出 / 验证目标 | 实测结果 | 观测细节 |
|---|---|---|---|
| fault:altered_quote | `unverified_quote` | PASS | `unverified_quote` |
| fault:dropped_negation | `negation_dropped` | PASS | `negation_dropped` |
| fault:altered_number | `number_not_in_evidence` | PASS | `number_not_in_evidence` |
| fault:unit_swap | `unit_mismatch` | PASS | `unit_mismatch` |
| fault:wrong_anchor | `anchor_relocated` | PASS | `anchor_relocated` |
| fault:may_as_asserted | `hedge_mismatch` | PASS | `hedge_mismatch` |
| fault:causal_overclaim | `causal_overclaim` | PASS | `causal_overclaim` |
| fault:over_broad_assertion | `over_broad_assertion` | PASS | `number_not_in_evidence, over_broad_assertion` |
| happy_closed_and_rdf | 契约零错 + 三格式 RDF 一致 | PASS | `exact=4` |
| nested_4graph_contract | 嵌套四图封闭契约投影 | PASS | `issues=0` |
| open_contract_no_clobber | 开放契约无覆盖 + 列表 claims | PASS | `issues=0` |
| multi_format_jats_multipaper_iri | JATS 表格 + 多篇元数据隔离 + 安全 IRI | PASS | `tables=1, p2=Paper Beta` |
| conflict_and_dedup | 去重 + 极性/反义谓词冲突检测 | PASS | `dedup=1, conflicts=['antonym_predicate_conflict', 'polarity_conflict']` |
| encrypted_pdf_fallback | 状态降级为 unable/partial | PASS | `status=unable` |
