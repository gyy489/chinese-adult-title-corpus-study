"""Rule 009: exclude confirmed long-form/forum-metadata records.

Background: rule 003 v1.0 wrongly excluded 7,166 "{name}{number}" records
as serial fiction and was retracted (see docs/cleaning_rules.md rule 003) --
most of that pattern turned out to be legitimate reposted/renumbered videos.
During that retraction's investigation, two much narrower signals were
identified as genuinely reliable indicators of non-video text: title length
> 200 characters (25 records) and explicit forum-post metadata such as
"作者：#"/"发帖时间：" (12 records), 31 records total after de-overlap. These
were written to titles_long_or_forum_flagged_for_review.csv as a flagged-
but-not-excluded list.

The user reviewed that list directly and confirmed: these are novels/forum
posts with little analytical value, safe to exclude. This script formalizes
that confirmation as an actual exclusion, using fields separate from rule
003's (retracted, always-true) retained/exclusion_reason so the two rules'
provenance never gets conflated.

Applied on top of data/interim/04_deduplicated/titles_deduplicated.jsonl,
overwritten in place. Nothing is deleted: every row stays present with
`excluded_by_rule_009` and `rule_009_exclusion_reason`.
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

FORUM_METADATA_MARKERS = ["作者：#", "发帖时间："]
LONG_TITLE_THRESHOLD = 200


def classify(original_title: str) -> str | None:
    reasons = []
    if len(original_title) > LONG_TITLE_THRESHOLD:
        reasons.append("length_over_200_chars")
    if any(m in original_title for m in FORUM_METADATA_MARKERS):
        reasons.append("explicit_forum_metadata")
    return "+".join(reasons) if reasons else None


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
        reason = classify(rec["original_title"])
        if reason:
            n_excluded += 1
        out_rows.append(
            {
                **rec,
                "excluded_by_rule_009": reason is not None,
                "rule_009_exclusion_reason": reason,
            }
        )

    with DEDUP_PATH.open("w", encoding="utf-8") as f:
        for rec in out_rows:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"total_records={len(out_rows)}")
    print(f"excluded_by_rule_009={n_excluded}")
    print(f"output={DEDUP_PATH.relative_to(PROJECT_ROOT)} sha256={sha256_of(DEDUP_PATH)}")


if __name__ == "__main__":
    main()
