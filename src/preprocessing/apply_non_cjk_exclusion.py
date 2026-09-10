"""Rule 011: exclude non-CJK titles from the formal analysis dataset.

Rule 006 added an informational `contains_cjk` flag (51 records are false)
without excluding anything, deliberately -- see docs/cleaning_rules.md rule
006, which cites rule 003's retraction as the reason to default to flagging
over excluding. The user has now reviewed
data/interim/04_deduplicated/non_cjk_titles_for_review.csv directly and
confirmed (2026-07-31): discard the foreign-language ones. This script
formalizes that as an actual exclusion from the analysis-ready dataset,
using its own field pair (excluded_by_rule_011/rule_011_exclusion_reason)
so it never gets confused with rule 006's original flag or rule 009's
unrelated exclusion criteria.

Applied on top of data/interim/04_deduplicated/titles_deduplicated.jsonl,
overwritten in place. Nothing is deleted: all 25,191 rows stay present.
"""

from __future__ import annotations

import json
from pathlib import Path

try:
    from .extract_bracket_content import sha256_of
except ImportError:  # Direct script execution.
    from extract_bracket_content import sha256_of

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEDUP_PATH = PROJECT_ROOT / "data" / "interim" / "04_deduplicated" / "titles_deduplicated.jsonl"


def main() -> None:
    rows = []
    with DEDUP_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    n_excluded = 0
    out_rows = []
    for rec in rows:
        excluded = not rec["contains_cjk"]
        if excluded:
            n_excluded += 1
        out_rows.append(
            {
                **rec,
                "excluded_by_rule_011": excluded,
                "rule_011_exclusion_reason": "non_cjk_title" if excluded else None,
            }
        )

    with DEDUP_PATH.open("w", encoding="utf-8") as f:
        for rec in out_rows:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"total_records={len(out_rows)}")
    print(f"excluded_by_rule_011={n_excluded}")
    print(f"output={DEDUP_PATH.relative_to(PROJECT_ROOT)} sha256={sha256_of(DEDUP_PATH)}")


if __name__ == "__main__":
    main()
