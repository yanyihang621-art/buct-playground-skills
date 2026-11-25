# Building editorial notes

**Illustrative teaching example.** The people, materials and snippets below are invented for interface demonstration. Use only the current case at execution time.

Consider two supplied reports on measurement drift. Report A says “Calibration was evaluated on stationary sensors.” Report B says “Mobile sensors were evaluated indoors.” A traceable comparison can say that A holds placement fixed and B varies placement within an indoor setting. Together they leave drift during outdoor movement unresolved **within this pair of reports**. The comparison alone gives no grounds for claiming that all mobile systems fail outdoors.

If the author's supplied protocol says “We recorded mobile sensors on an outdoor route,” the contribution is a test under that condition. An accuracy improvement would require an observed result and comparator. With that evidence, write an ordinary transition into the contribution, e.g. “We therefore measure drift along an outdoor route, extending the evaluation to moving sensors exposed to outdoor conditions.” Without the protocol, keep the gap and record the unavailable evidence.

## Analysis JSON

The following excerpt demonstrates the interface. Replace source IDs with IDs from the active case. The example intentionally omits the second paper card. Add one card for each actual work used; reuse metadata already indexed. A source ID names one passage. A quotation copied from another passage will fail. Multiple short quotations can support different parts of a row.

```json
{
  "paper_annotations": [
    {"cite_key":"reportA","title":"Stationary calibration","route":"Fixed installation","problem_setting":"Calibration under fixed placement","evaluation_system":"Stationary sensors","limitation":"Placement did not vary in this evaluation","evidence":[{"source_id":"S0001","quote":"Calibration was evaluated on stationary sensors."}]}
  ],
  "research_gaps": [
    {"gap_id":"G1","gap_statement":"Neither report tests drift during outdoor movement.","derived_from_cite_keys":["reportA","reportB"],"author_judgment":false,"evidence":[{"source_id":"S0001","quote":"Calibration was evaluated on stationary sensors."},{"source_id":"S0002","quote":"Mobile sensors were evaluated indoors."}]}
  ],
  "contributions": [
    {"contrib_id":"C1","claim":"We measure drift along an outdoor route.","addresses_gap_id":"G1","evidence_anchor":{"method_ref":"Field protocol"},"evidence":[{"source_id":"S0003","quote":"We recorded mobile sensors on an outdoor route."}]}
  ],
  "claim_evidence": [
    {"kind":"comparison","claim":"The mobile evaluation was restricted to an indoor setting","cite_keys":["reportB"],"evidence":[{"source_id":"S0002","quote":"Mobile sensors were evaluated indoors."}]}
  ],
  "section_spans":{"contribution":"We measure drift along an outdoor route."},
  "change_records":[{"action":"narrowed_claim","before":"improves outdoor accuracy","after":"measures drift along an outdoor route","reason":"The protocol describes a measurement design."}],
  "uncertainties":[],
  "contract_fields":{},
  "artifact_names":{"md":"intro_and_related_work.md"}
}
```

`section_spans` accepts background, related_work, gap and contribution. `claim` should reproduce a phrase in the delivered manuscript. Preserve the detailed audit separately so the reader sees normal citations and clean prose. When the task explicitly requests citation labels, paired languages, an abstract, revision notes or another structured field, add the real deliverable in that field. The result and relative delivery paths should be sufficient for an independent reader to find it.
