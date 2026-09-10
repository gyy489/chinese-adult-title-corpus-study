"""Export human-review-friendly CSVs from
data/interim/03_filtered/titles_filtered.jsonl into the same folder.

Rule 003 v1.0 (exclude by "{name}{number}" pattern) was retracted on
2026-07-31 -- see filter_serial_story_posts.py's module docstring and
docs/cleaning_rules.md rule 003. Every record is retained now, so this
script no longer splits retained/excluded. Instead it exports:

  - titles_all_for_review.csv -- all 25,191 records (record_id, source_site,
    original_title, cleaned_title), the current full candidate corpus.
  - titles_long_or_forum_flagged_for_review.csv -- a small, separate,
    NOT-excluded flag list of the two signals that actually did distinguish
    real outliers during the retraction investigation: title length > 200
    chars, or explicit forum-post metadata ("作者：#"/"发帖时间："). Only 25
    + 12 records total (some overlap) -- worth a manual look precisely
    because it's small, unlike the retracted 7,166-record exclusion.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FILTERED_DIR = PROJECT_ROOT / "data" / "interim" / "03_filtered"
INPUT_PATH = FILTERED_DIR / "titles_filtered.jsonl"

FORUM_METADATA_MARKERS = ["作者：#", "发帖时间："]
LONG_TITLE_THRESHOLD = 200


def main() -> None:
    rows = []
    with INPUT_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    all_path = FILTERED_DIR / "titles_all_for_review.csv"
    flagged_path = FILTERED_DIR / "titles_long_or_forum_flagged_for_review.csv"

    with all_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["record_id", "source_site", "original_title", "cleaned_title"]
        )
        writer.writeheader()
        for r in rows:
            writer.writerow(
                {
                    "record_id": r["record_id"],
                    "source_site": r["source_site"],
                    "original_title": r["original_title"],
                    "cleaned_title": r["cleaned_title"],
                }
            )

    n_flagged = 0
    with flagged_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["record_id", "source_site", "original_title", "flag_reason", "title_length"]
        )
        writer.writeheader()
        for r in rows:
            t = r["original_title"]
            reasons = []
            if len(t) > LONG_TITLE_THRESHOLD:
                reasons.append("length_over_200_chars")
            if any(m in t for m in FORUM_METADATA_MARKERS):
                reasons.append("explicit_forum_metadata")
            if reasons:
                n_flagged += 1
                writer.writerow(
                    {
                        "record_id": r["record_id"],
                        "source_site": r["source_site"],
                        "original_title": t,
                        "flag_reason": "+".join(reasons),
                        "title_length": len(t),
                    }
                )

    print(f"all_rows={len(rows)} -> {all_path.relative_to(PROJECT_ROOT)}")
    print(f"flagged_rows={n_flagged} (NOT excluded, just flagged) -> {flagged_path.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
