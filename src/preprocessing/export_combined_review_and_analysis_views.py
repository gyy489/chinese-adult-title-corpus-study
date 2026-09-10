"""Export review-friendly views and the recommended analysis-ready subset
from data/interim/10_combined_deduplicated/titles_combined_deduplicated.jsonl
(103,743 round1+round2 records). Structured like round1's
`export_review_and_analysis_views.py`, extended with a `data_round` column
(round1/round2) throughout, since separating the two crawls is the whole
point of this round2-focused layer.

  - duplicate_groups_for_review.csv -- every record belonging to a
    duplicate_group_size > 1 group, sorted so reposts of the same title sit
    next to each other, for eyeballing whether they look like genuine
    reposts (this project's standing convention: repost is common in this
    industry, per rule 007/010).
  - cross_round_duplicate_groups_for_review.csv -- the subset of the above
    restricted to groups that contain BOTH a round1 and a round2 member
    (i.e. a round2 title collided, byte-for-byte, with an existing round1
    title). This is the specific new phenomenon round1's own isolated dedup
    run could not detect.
  - foreign_performer_titles_for_review.csv -- every record excluded by
    rule 016 (same four signals as round1, unchanged detector), for
    spot-checking, split by data_round in the same file.
  - long_or_forum_titles_for_review.csv -- every record excluded by rule 009
    (title length > 200 chars, or explicit forum-post metadata "作者：#"/
    "发帖时间："), split by data_round in the same file. round1's 31 hits are
    copied verbatim from its own frozen judgment (see apply_length_forum_
    exclusion_combined.py); round2's 11 hits (all length_over_200_chars,
    none hit the forum-metadata markers) were newly computed and confirmed
    by the user 2026-08-04 -- see docs/research_log.md.
  - non_cjk_titles_for_review_combined.csv -- every record excluded by rule
    011 (`contains_cjk=false`), split by data_round in the same file.
    round1's 51 hits are copied verbatim from its own frozen judgment;
    round2's 28 hits were newly computed -- see apply_non_cjk_exclusion_
    combined.py's docstring for why this is 28, not the "20条" figure
    relayed at task hand-off (a real discrepancy, reported rather than
    forced to match).
  - titles_for_analysis_combined.csv -- the recommended "one row per video"
    subset: is_duplicate_canonical=true AND not excluded_by_rule_009 AND
    not excluded_by_rule_011 AND not excluded_by_rule_016, including
    `cleaned_title` (rules 001/002/004/005/006/008/012/013/014, plus the
    round2 017 source_08-AI-residue cleanup, all applied -- see docs/
    research_log.md 2026-08-02/2026-08-03 entries). This filter formula
    now matches round1's own titles_for_analysis.csv exactly (2026-08-04:
    rules 009/011, previously deferred, are now applied to round2 -- see
    apply_length_forum_exclusion_combined.py/apply_non_cjk_exclusion_
    combined.py, which must run before this script).

    This export is also where the empty-title safety net (rule 018) is
    applied for combined records -- see src/preprocessing/empty_title_
    fallback.py. It runs here, not inside apply_full_cleanup_pipeline_
    combined.py's rules 001-014 chain, for the same reason rule 015
    (organizer-prefix removal) is round1's own separate downstream step
    rather than a retroactive edit to that shared chain: `cleaned_title` in
    titles_combined_deduplicated.jsonl must stay byte-for-byte identical to
    round1's own frozen rules-001-014 output (the "round1 invariant" tests/
    test_full_cleanup_pipeline_combined.py checks). This export -- the
    actual "final, analysis-ready" cleaned_title people use downstream for
    combined records -- is the right place, exactly parallel to how round1's
    OWN final analysis-ready cleaned_title (after rule 015 AND this same
    fallback) lives only in data/interim/04_organizer_prefix_cleaned/
    titles_for_analysis.csv, never retroactively patched back into
    02_normalized/04_deduplicated either.

All exports are for convenience; the full audit trail with every record and
every flag (including the PRE-fallback `cleaned_title`) stays in
titles_combined_deduplicated.jsonl untouched.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PREPROCESSING_DIR = Path(__file__).resolve().parent
if str(PREPROCESSING_DIR) not in sys.path:
    sys.path.insert(0, str(PREPROCESSING_DIR))

from empty_title_fallback import apply_empty_title_fallback  # noqa: E402

COMBINED_DIR = PROJECT_ROOT / "data" / "interim" / "10_combined_deduplicated"
COMBINED_PATH = COMBINED_DIR / "titles_combined_deduplicated.jsonl"


def main() -> None:
    rows = []
    with COMBINED_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    dup_fieldnames = [
        "duplicate_group_id",
        "duplicate_group_size",
        "is_duplicate_canonical",
        "data_round",
        "source_site",
        "crawl_time",
        "record_id",
        "original_title",
    ]

    # --- full duplicate-groups review export ---
    dup_path = COMBINED_DIR / "duplicate_groups_for_review.csv"
    dup_rows = [r for r in rows if r["duplicate_group_size"] > 1]
    dup_rows.sort(key=lambda r: (-r["duplicate_group_size"], r["duplicate_group_id"], r["crawl_time"]))
    with dup_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=dup_fieldnames)
        writer.writeheader()
        for r in dup_rows:
            writer.writerow({k: r[k] for k in dup_fieldnames})

    # --- cross-round subset (groups with >=1 round1 member AND >=1 round2 member) ---
    cross_round_path = COMBINED_DIR / "cross_round_duplicate_groups_for_review.csv"
    groups_by_id: dict[str, list[dict]] = {}
    for r in dup_rows:
        groups_by_id.setdefault(r["duplicate_group_id"], []).append(r)
    cross_round_group_ids = {
        gid
        for gid, members in groups_by_id.items()
        if {m["data_round"] for m in members} == {"round1", "round2"}
    }
    cross_round_rows = [r for r in dup_rows if r["duplicate_group_id"] in cross_round_group_ids]
    with cross_round_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=dup_fieldnames)
        writer.writeheader()
        for r in cross_round_rows:
            writer.writerow({k: r[k] for k in dup_fieldnames})

    # --- foreign-performer exclusion review export (rule 016) ---
    foreign_performer_path = COMBINED_DIR / "foreign_performer_titles_for_review.csv"
    foreign_performer_rows = [r for r in rows if r["excluded_by_rule_016"]]
    foreign_performer_fieldnames = [
        "record_id",
        "source_site",
        "data_round",
        "original_title",
        "rule_016_exclusion_reason",
    ]
    with foreign_performer_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=foreign_performer_fieldnames)
        writer.writeheader()
        for r in foreign_performer_rows:
            writer.writerow({k: r[k] for k in foreign_performer_fieldnames})

    # --- long/forum exclusion review export (rule 009) ---
    long_or_forum_path = COMBINED_DIR / "long_or_forum_titles_for_review.csv"
    long_or_forum_rows = [r for r in rows if r["excluded_by_rule_009"]]
    long_or_forum_fieldnames = [
        "record_id",
        "source_site",
        "data_round",
        "original_title",
        "rule_009_exclusion_reason",
        "title_length",
    ]
    with long_or_forum_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=long_or_forum_fieldnames)
        writer.writeheader()
        for r in long_or_forum_rows:
            writer.writerow(
                {
                    "record_id": r["record_id"],
                    "source_site": r["source_site"],
                    "data_round": r["data_round"],
                    "original_title": r["original_title"],
                    "rule_009_exclusion_reason": r["rule_009_exclusion_reason"],
                    "title_length": len(r["original_title"]),
                }
            )

    # --- non-CJK exclusion review export (rule 011) ---
    non_cjk_combined_path = COMBINED_DIR / "non_cjk_titles_for_review_combined.csv"
    non_cjk_combined_rows = [r for r in rows if r["excluded_by_rule_011"]]
    non_cjk_combined_fieldnames = [
        "record_id",
        "source_site",
        "data_round",
        "original_title",
        "cleaned_title",
        "rule_011_exclusion_reason",
    ]
    with non_cjk_combined_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=non_cjk_combined_fieldnames)
        writer.writeheader()
        for r in non_cjk_combined_rows:
            writer.writerow({k: r[k] for k in non_cjk_combined_fieldnames})

    # --- recommended analysis-ready export ---
    # Empty-title safety net (rule 018, src/preprocessing/empty_title_
    # fallback.py) applied here -- see module docstring above for why this
    # export, not the shared 001-014 recompute, is the right place.
    analysis_path = COMBINED_DIR / "titles_for_analysis_combined.csv"
    analysis_rows = [
        r
        for r in rows
        if r["is_duplicate_canonical"]
        and not r["excluded_by_rule_009"]
        and not r["excluded_by_rule_011"]
        and not r["excluded_by_rule_016"]
    ]
    analysis_fieldnames = [
        "record_id",
        "source_site",
        "data_round",
        "original_title",
        "cleaned_title",
        "contains_cjk",
        "duplicate_group_size",
        "crawl_time",
        "cleaned_title_before_empty_title_fallback",
        "empty_title_fallback_applied",
        "empty_title_fallback_source",
    ]
    n_empty_title_fallback_applied = 0
    with analysis_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=analysis_fieldnames)
        writer.writeheader()
        for r in analysis_rows:
            fallback = apply_empty_title_fallback(r["original_title"], r["cleaned_title"])
            if fallback["empty_title_fallback_applied"]:
                n_empty_title_fallback_applied += 1
            out_row = {k: r[k] for k in analysis_fieldnames if k in r}
            out_row["cleaned_title"] = fallback["cleaned_title"]
            out_row["cleaned_title_before_empty_title_fallback"] = fallback[
                "cleaned_title_before_empty_title_fallback"
            ]
            out_row["empty_title_fallback_applied"] = fallback["empty_title_fallback_applied"]
            out_row["empty_title_fallback_source"] = fallback["empty_title_fallback_source"]
            writer.writerow(out_row)

    n_dup_groups = len({r["duplicate_group_id"] for r in dup_rows})
    print(f"duplicate_group_member_rows={len(dup_rows)} ({n_dup_groups} groups) -> {dup_path.relative_to(PROJECT_ROOT)}")
    print(
        f"cross_round_duplicate_group_member_rows={len(cross_round_rows)} "
        f"({len(cross_round_group_ids)} groups) -> {cross_round_path.relative_to(PROJECT_ROOT)}"
    )
    print(f"foreign_performer_rows={len(foreign_performer_rows)} -> {foreign_performer_path.relative_to(PROJECT_ROOT)}")
    print(f"long_or_forum_rows={len(long_or_forum_rows)} -> {long_or_forum_path.relative_to(PROJECT_ROOT)}")
    print(f"non_cjk_combined_rows={len(non_cjk_combined_rows)} -> {non_cjk_combined_path.relative_to(PROJECT_ROOT)}")
    print(f"analysis_ready_rows={len(analysis_rows)} -> {analysis_path.relative_to(PROJECT_ROOT)}")
    print(f"empty_title_fallback_applied={n_empty_title_fallback_applied}")


if __name__ == "__main__":
    main()
