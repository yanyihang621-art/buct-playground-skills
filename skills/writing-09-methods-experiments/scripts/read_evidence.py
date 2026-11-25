"""Navigate the indexed experiment materials without dumping entire papers into context."""

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re

from source_labels import label_for

TOPICS = {
    "procedure": r"protocol|prepar|preprocess|algorithm|procedure|align|filter|exclude|流程|步骤|预处理|筛选|剔除",
    "setup": r"software|hardware|version|environment|instrument|apparatus|package|seed|replicat|repeat|环境|仪器|重复|种子|批次",
    "data": r"data.?set|cohort|partition|split|sample|preprocess|held.out|subject|fold|数据|样本|划分|受试|分组",
    "control": r"baseline|control|budget|ablation|metric|uncertainty|interval|statistic|对照|基线|消融|指标|区间|方差",
    "reproduce": r"commit|version|command|python|--config|archive|accession|doi|repository|复现|版本|命令|存档",
    "limits": r"restrict|exclude|fail|limit|bias|missing|calibrat|noise|阈值|偏差|失效|缺失|适用|限制",
}


def select_rows(rows, topic, query, source):
    pattern = re.compile(TOPICS[topic], re.I) if topic else None
    terms = query.casefold().split()
    by_source = defaultdict(list)
    for row in rows:
        if source and row["source"] != source:
            continue
        passage = row["text"]
        haystack = (row["locator"] + " " + passage).casefold()
        if pattern and not pattern.search(haystack):
            continue
        if not all(term in haystack for term in terms):
            continue
        by_source[row["source"]].append(row)
    # Round-robin so a long narrative cannot hide the configuration or run log.
    streams = [iter(v) for v in by_source.values()]
    while streams:
        active = []
        for stream in streams:
            row = next(stream, None)
            if row is not None:
                yield row
                active.append(stream)
        streams = active


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--facts", required=True)
    p.add_argument("--topic", choices=TOPICS)
    p.add_argument("--query", default="")
    p.add_argument("--source", default="")
    p.add_argument("--passages", action="store_true", help="Show material passages and their citation IDs.")
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--limit", type=int, default=18)
    opts = p.parse_args()
    facts = json.loads(Path(opts.facts).read_text(encoding="utf-8"))
    rows = facts.get("evidence", [])
    if not (opts.topic or opts.query or opts.source or opts.passages):
        print(json.dumps({"passages_per_source": Counter(r["source"] for r in rows),
                          "ingestion_issues": facts.get("ingestion_issues", []),
                          "topics": list(TOPICS)}, ensure_ascii=False))
        return
    from itertools import islice
    start = max(opts.offset, 0)
    for row in islice(select_rows(rows, opts.topic, opts.query, opts.source), start, start + max(opts.limit, 0)):
        print(json.dumps({"id": label_for(row), **{k: row[k] for k in ("source", "locator", "text")}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
