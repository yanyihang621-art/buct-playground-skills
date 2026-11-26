"""Print a window of the scoped source index for the translating Agent."""
import argparse
import json
from pathlib import Path

from segment_and_protect import sentence_spans


def read_batch(segments, start, count):
    if start < 0 or count < 1:
        raise ValueError("start must be nonnegative and count must be positive")
    stop = min(start + count, len(segments))
    rows = []
    for row in segments[start:stop]:
        item = {key: row[key] for key in ("segment_id", "role", "section_path", "masked_text",
                                         "protected_tokens", "source_locator") if key in row}
        item["source_sentence_count"] = len(sentence_spans(row["source_text"]))
        rows.append(item)
    return {"total": len(segments), "next_start": stop if stop < len(segments) else None,
            "preceding_context": segments[start - 1]["source_text"][-400:] if 0 < start <= len(segments) else "",
            "following_context": segments[stop]["source_text"][:400] if stop < len(segments) else "",
            "segments": rows}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--segments", type=Path, required=True)
    parser.add_argument("--start", type=int, default=0, help="Zero-based offset; use next_start for the next window")
    parser.add_argument("--count", type=int, default=16)
    args = parser.parse_args()
    index = json.loads(args.segments.read_text(encoding="utf-8"))
    print(json.dumps(read_batch(index["segments"], args.start, args.count), ensure_ascii=False))
