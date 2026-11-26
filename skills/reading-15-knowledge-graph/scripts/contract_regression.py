"""Local regression for KG contract shapes, subset selection and immutable generated fields."""
import json

from build_kg_result import build_kg_result
from validate_kg import validate_kg


def run():
    graph = {"status": "success", "nodes": [], "edges": []}
    corpus = {"papers": []}

    def assemble(contract, override=None):
        case = {"case_id": "synthetic-contract", "output_contract": contract}
        result = build_kg_result(case, graph, [], override_data=override)
        return result, validate_kg(case, result, graph, corpus, [])

    schema = {"required": ["evidence_count", "annotation"], "properties": {
        "evidence_count": {"type": "integer"}, "annotation": {"type": "object", "required": ["items"],
            "properties": {"items": {"type": "array", "items": {"type": "number"}}}},
        "unused_optional": {"type": "string"}}}
    actual = {"evidence_count": 2, "annotation": {"items": [1.5, 2]}}
    result, report = assemble(schema, actual)
    assert report["pass"] and "unused_optional" not in result and result["annotation"] == actual["annotation"]
    wrong, report = assemble(schema, {"evidence_count": True, "annotation": {"items": ["two"]}})
    assert wrong["status"] == "partial" and len(report["errors"]) == 2
    assert any("annotation.items[0]" in msg for msg in report["errors"])
    missing, report = assemble(schema, {"evidence_count": 1})
    assert "annotation" in missing and missing["annotation"] is None and len(report["errors"]) == 1

    descriptor = [{"field": "entities", "type": "array"}, {"name": "interpretation", "type": "string"},
                  {"key": "optional_detail", "type": "string", "required": False}]
    checked, report = assemble(descriptor, {"interpretation": "No paper was provided in this synthetic case."})
    assert report["pass"] and checked["entities"] == [] and "optional_detail" not in checked
    for contract in (["case_id", "status", "edges"], {"fields": ["case_id", "status", "edges"]}):
        result, report = assemble(contract)
        assert report["pass"] and result["status"] == "success"
    nullable = {"required": ["comment"], "properties": {"comment": {"type": ["null", "string"]}}}
    assert assemble(nullable, {"comment": None})[1]["pass"]
    assert not assemble(nullable)[1]["pass"]
    try:
        assemble(["nodes"], {"nodes": [{"label": "unsupported amendment"}]})
    except ValueError:
        pass
    else:
        raise AssertionError("graph field override was accepted")
    return {"passed": True, "cases": ["typed values", "nested type errors", "required missing", "optional absent",
            "named descriptors", "key lists", "explicit nullable", "graph override blocked"]}


if __name__ == "__main__":
    print(json.dumps(run()))
