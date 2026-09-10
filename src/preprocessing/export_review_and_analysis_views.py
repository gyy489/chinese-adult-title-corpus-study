"""Export review-friendly views the user asked to see, plus the resulting
analysis-ready subset, all from data/interim/04_deduplicated/titles_deduplicated.jsonl.

  - non_cjk_titles_for_review.csv -- the 51 contains_cjk=false records.
  - duplicate_groups_for_review.csv -- every record belonging to a
    duplicate_group_size > 1 group (983 rows across 368 groups), grouped
    together and sorted so reposts of the same title sit next to each
    other, for the user to eyeball whether they look like genuine reposts.
  - foreign_performer_titles_for_review.csv -- the 249 records excluded by
    rule 016 (Japanese-script residue, an explicit "中文字幕" marker, or the
    "兔子先生" brand name), for after-the-fact spot-checking. The user
    decided to exclude these outright rather than queue them for row-by-row
    review, but this export is kept anyway per this project's standing
    audit-trail convention (every exclusion gets a reviewable export, even
    when the decision itself isn't in question).
  - titles_for_analysis.csv -- the practical "one row per video" dataset:
    is_duplicate_canonical=true AND not excluded_by_rule_009 AND not
    excluded_by_rule_011 AND not excluded_by_rule_016. Per the user's
    decisions (2026-07-31 through 2026-08-02): duplicate groups count as one
    video (repost is common in this industry), confirmed long-form/forum
    posts are excluded, non-CJK titles are discarded, and titles centering a
    foreign (chiefly Japanese) performer are out of this project's "Chinese
    pornographic video titles" research scope.

All exports are for convenience; the full audit trail with every record and
every flag stays in titles_deduplicated.jsonl untouched.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEDUP_PATH = PROJECT_ROOT / "data" / "interim" / "04_deduplicated" / "titles_deduplicated.jsonl"
OUTPUT_DIR = PROJECT_ROOT / "data" / "interim" / "04_deduplicated"


def main() -> None:
    rows = []
    with DEDUP_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    # --- non-CJK review export ---
    non_cjk_path = OUTPUT_DIR / "non_cjk_titles_for_review.csv"
    non_cjk_rows = [r for r in rows if not r["contains_cjk"]]
    with non_cjk_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["record_id", "source_site", "original_title", "cleaned_title"])
        writer.writeheader()
        for r in non_cjk_rows:
            writer.writerow(
                {
                    "record_id": r["record_id"],
                    "source_site": r["source_site"],
                    "original_title": r["original_title"],
                    "cleaned_title": r["cleaned_title"],
                }
            )

    # --- duplicate groups review export ---
    dup_path = OUTPUT_DIR / "duplicate_groups_for_review.csv"
    dup_rows = [r for r in rows if r["duplicate_group_size"] > 1]
    dup_rows.sort(key=lambda r: (-r["duplicate_group_size"], r["duplicate_group_id"], r["crawl_time"]))
    with dup_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "duplicate_group_id",
                "duplicate_group_size",
                "is_duplicate_canonical",
                "source_site",
                "crawl_time",
                "record_id",
                "original_title",
            ],
        )
        writer.writeheader()
        for r in dup_rows:
            writer.writerow(
                {
                    "duplicate_group_id": r["duplicate_group_id"],
                    "duplicate_group_size": r["duplicate_group_size"],
                    "is_duplicate_canonical": r["is_duplicate_canonical"],
                    "source_site": r["source_site"],
                    "crawl_time": r["crawl_time"],
                    "record_id": r["record_id"],
                    "original_title": r["original_title"],
                }
            )

    # --- foreign-performer exclusion review export (rule 016) ---
    foreign_performer_path = OUTPUT_DIR / "foreign_performer_titles_for_review.csv"
    foreign_performer_rows = [r for r in rows if r["excluded_by_rule_016"]]
    with foreign_performer_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "record_id",
                "source_site",
                "original_title",
                "cleaned_title",
                "rule_016_exclusion_reason",
            ],
        )
        writer.writeheader()
        for r in foreign_performer_rows:
            writer.writerow(
                {
                    "record_id": r["record_id"],
                    "source_site": r["source_site"],
                    "original_title": r["original_title"],
                    "cleaned_title": r["cleaned_title"],
                    "rule_016_exclusion_reason": r["rule_016_exclusion_reason"],
                }
            )

    # --- analysis-ready "one row per video" export ---
    analysis_path = OUTPUT_DIR / "titles_for_analysis.csv"
    analysis_rows = [
        r
        for r in rows
        if r["is_duplicate_canonical"]
        and not r["excluded_by_rule_009"]
        and not r["excluded_by_rule_011"]
        and not r["excluded_by_rule_016"]
    ]
    with analysis_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "record_id",
                "source_site",
                "original_title",
                "cleaned_title",
                "duplicate_group_size",
                "contains_cjk",
            ],
        )
        writer.writeheader()
        for r in analysis_rows:
            writer.writerow(
                {
                    "record_id": r["record_id"],
                    "source_site": r["source_site"],
                    "original_title": r["original_title"],
                    "cleaned_title": r["cleaned_title"],
                    "duplicate_group_size": r["duplicate_group_size"],
                    "contains_cjk": r["contains_cjk"],
                }
            )

    n_dup_groups = len({r["duplicate_group_id"] for r in dup_rows})
    print(f"non_cjk_rows={len(non_cjk_rows)} -> {non_cjk_path.relative_to(PROJECT_ROOT)}")
    print(f"duplicate_group_member_rows={len(dup_rows)} ({n_dup_groups} groups) -> {dup_path.relative_to(PROJECT_ROOT)}")
    print(f"foreign_performer_rows={len(foreign_performer_rows)} -> {foreign_performer_path.relative_to(PROJECT_ROOT)}")
    print(f"analysis_ready_rows={len(analysis_rows)} -> {analysis_path.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
