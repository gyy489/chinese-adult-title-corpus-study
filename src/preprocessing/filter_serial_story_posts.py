"""Rule 003 v1.0 is RETRACTED (2026-07-31). Read this before touching the
output of this script.

v1.0 (2026-07-30) excluded 7,166 records (28.45% of the corpus) whose title
began with "{recurring name}{whitespace}{number}", assuming the number was a
serialized-story chapter/episode index and the record was therefore forum
fiction, not a video title.

The user reviewed data/interim/03_filtered/titles_excluded_for_review.csv
directly and reported that most of the 7,166 excluded rows are valid data,
and that the number is a website/upload-numbering convention with a
different meaning than a story chapter -- and that "duplicate" should mean
identical full title text only, not "shares a recurring name prefix."

Re-checking the excluded set confirmed this: length distribution is
median=31 / p90=41 / p99=52 -- essentially indistinguishable from the
corpus-wide title length profile (overall median=32, p95=60) -- not the
long rambling prose the v1.0 rationale implied. Only 25 records (0.3%) are
actually long-form (>200 chars), and only 12 contain explicit forum-post
metadata ("作者：#"/"发帖时间："). The "{name}{number}" pattern is much more
consistent with sequential upload numbering under a recurring creator/persona
name (the same structure as "麻豆传媒MD-0275" in rule 002, just without a
separate company brand) than with serialized fiction. v1.0's conclusion was
drawn from a small number of extreme-length outliers found during the
original raw-data audit and over-generalized to the whole pattern.

v1.1 (this version) therefore does NOT exclude anything. It still runs the
original detection logic and records what WOULD have matched under v1.0, as
`matched_series_name_pattern_v1_retracted`, purely so the retraction itself
stays auditable -- but `retained` is now always true. See docs/cleaning_rules.md
rule 003 for the full retraction writeup, and docs/research_log.md for the
correction record.

Exact-duplicate detection (the definition of "duplicate" the user actually
wants -- identical full title text) already exists and does not need this
script: see the "duplicate_title_values"/"duplicate_title_extra_rows" fields
in data/raw/manifest.json and data/interim/01_profile/raw_profile.json,
produced by profile_raw.py during the very first audit pass.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

try:
    from .extract_bracket_content import sha256_of
except ImportError:  # Direct script execution.
    from extract_bracket_content import sha256_of

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_PATH = PROJECT_ROOT / "data" / "raw" / "titles.jsonl"
MANIFEST_PATH = PROJECT_ROOT / "data" / "raw" / "manifest.json"
NORMALIZED_PATH = PROJECT_ROOT / "data" / "interim" / "02_normalized" / "titles_bracket_cleaned.jsonl"
OUTPUT_DIR = PROJECT_ROOT / "data" / "interim" / "03_filtered"
OUTPUT_PATH = OUTPUT_DIR / "titles_filtered.jsonl"

SERIES_NUMBER_RE = re.compile(r"^([一-鿿]{2,12})\s+(\d{1,4})\b")
MIN_DISTINCT_EPISODES = 3


def discover_series_names(records: list[dict]) -> set[str]:
    episode_numbers: dict[str, set[int]] = defaultdict(set)
    for rec in records:
        m = SERIES_NUMBER_RE.match(rec["title"])
        if m:
            episode_numbers[m.group(1)].add(int(m.group(2)))
    return {name for name, nums in episode_numbers.items() if len(nums) >= MIN_DISTINCT_EPISODES}


def build_broad_matcher(series_names: set[str]) -> re.Pattern:
    ordered = sorted(series_names, key=len, reverse=True)
    return re.compile(r"^(" + "|".join(re.escape(n) for n in ordered) + r")\s")


def main() -> None:
    raw_sha256 = sha256_of(RAW_PATH)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if raw_sha256 != manifest["sha256"]:
        raise SystemExit("SHA-256 mismatch against data/raw/manifest.json; refusing to run.")

    raw_records = []
    with RAW_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                raw_records.append(json.loads(line))

    series_names = discover_series_names(raw_records)
    broad_re = build_broad_matcher(series_names)

    normalized_by_url = {}
    with NORMALIZED_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rec = json.loads(line)
                normalized_by_url[rec["record_id"]] = rec

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    n_total = 0
    n_would_have_matched_v1 = 0

    out_rows = []
    for raw_rec in raw_records:
        n_total += 1
        norm = normalized_by_url[raw_rec["source_url"]]
        m = broad_re.match(raw_rec["title"])
        matched_series_v1 = m.group(1) if m is not None else None
        if matched_series_v1 is not None:
            n_would_have_matched_v1 += 1

        out_rows.append(
            {
                "record_id": raw_rec["source_url"],
                "source_site": raw_rec["source_site"],
                "original_title": raw_rec["title"],
                "title_after_bracket_rule": norm["title_after_bracket_rule"],
                "cleaned_title": norm["cleaned_title"],
                "contains_cjk": norm["contains_cjk"],
                "retained": True,
                "exclusion_reason": None,
                "matched_series_name_pattern_v1_retracted": matched_series_v1,
                "rule_id": "serial_story_post_filter",
                "rule_version": "v1.1-RETRACTED-2026-07-31",
            }
        )

    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        for rec in out_rows:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"input_sha256={raw_sha256}")
    print(f"total_records={n_total}")
    print("RULE 003 v1.0 IS RETRACTED -- retained=True for all records.")
    print(f"records_that_would_have_matched_v1_pattern={n_would_have_matched_v1} (informational only, not excluded)")
    print(f"output={OUTPUT_PATH.relative_to(PROJECT_ROOT)} sha256={sha256_of(OUTPUT_PATH)}")


if __name__ == "__main__":
    main()
