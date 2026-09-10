#!/usr/bin/env python3
"""Run normalization, filtering, and deduplication on synthetic records."""

from __future__ import annotations

import json
from pathlib import Path

from src.preprocessing.pipeline import deduplicate_final_text, load_rules, transform_records


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    records = json.loads(
        (ROOT / "examples" / "synthetic" / "titles.json").read_text(encoding="utf-8")
    )
    rules = load_rules(ROOT / "config" / "cleaning_rules_public.json")
    transformed = transform_records(records, rules)
    kept, counts = deduplicate_final_text(transformed)
    print(json.dumps({"counts": counts, "records": kept}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
