"""Rule 007: flag exact-duplicate titles (interim/04_deduplicated).

Per the user's explicit definition (2026-07-31): "只有名称相同的才算是重复"
-- only records whose ORIGINAL title string is byte-for-byte identical count
as duplicates. This was already measured during the very first raw-data
audit (data/raw/manifest.json: duplicate_title_values=368,
duplicate_title_extra_rows=615) but never actually acted on.

This is a FLAGGING operation, not a deletion -- consistent with every prior
rule in this pipeline (and the explicit lesson from rule 003's retraction:
default to flagging over excluding). Every record is kept; within each
group of identical original_title strings, exactly one is marked
`is_duplicate_canonical=true` (the earliest by crawl_time, ties broken by
record_id for determinism) and the rest `is_duplicate_canonical=false`, all
sharing `duplicate_group_size` and `duplicate_group_id`. Downstream text
analysis that wants one row per unique title text can filter on
`is_duplicate_canonical`; analysis that cares about repost frequency can use
`duplicate_group_size` directly instead of losing that information.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path

try:
    from .extract_bracket_content import sha256_of
except ImportError:  # Direct script execution.
    from extract_bracket_content import sha256_of

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_PATH = PROJECT_ROOT / "data" / "raw" / "titles.jsonl"
MANIFEST_PATH = PROJECT_ROOT / "data" / "raw" / "manifest.json"
FILTERED_PATH = PROJECT_ROOT / "data" / "interim" / "03_filtered" / "titles_filtered.jsonl"
OUTPUT_DIR = PROJECT_ROOT / "data" / "interim" / "04_deduplicated"
OUTPUT_PATH = OUTPUT_DIR / "titles_deduplicated.jsonl"


def group_id_for(title: str) -> str:
    return hashlib.sha256(title.encode("utf-8")).hexdigest()[:12]


def main() -> None:
    raw_sha256 = sha256_of(RAW_PATH)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if raw_sha256 != manifest["sha256"]:
        raise SystemExit("SHA-256 mismatch against data/raw/manifest.json; refusing to run.")

    crawl_time_by_url = {}
    with RAW_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rec = json.loads(line)
                crawl_time_by_url[rec["source_url"]] = rec["crawl_time"]

    rows = []
    with FILTERED_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    groups: dict[str, list[dict]] = defaultdict(list)
    for rec in rows:
        groups[rec["original_title"]].append(rec)

    out_rows = []
    n_duplicate_groups = 0
    n_extra_rows = 0
    for title, group in groups.items():
        group_size = len(group)
        gid = group_id_for(title)
        if group_size > 1:
            n_duplicate_groups += 1
            n_extra_rows += group_size - 1
        ordered = sorted(
            group, key=lambda r: (crawl_time_by_url[r["record_id"]], r["record_id"])
        )
        for i, rec in enumerate(ordered):
            out_rows.append(
                {
                    **rec,
                    "duplicate_group_id": gid,
                    "duplicate_group_size": group_size,
                    "is_duplicate_canonical": i == 0,
                    "crawl_time": crawl_time_by_url[rec["record_id"]],
                }
            )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        for rec in out_rows:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    n_canonical = sum(1 for r in out_rows if r["is_duplicate_canonical"])
    print(f"input_sha256={raw_sha256}")
    print(f"total_records={len(out_rows)}")
    print(f"duplicate_title_groups={n_duplicate_groups}")
    print(f"extra_rows_beyond_first_in_group={n_extra_rows}")
    print(f"canonical_rows (is_duplicate_canonical=true)={n_canonical}")
    print(f"output={OUTPUT_PATH.relative_to(PROJECT_ROOT)} sha256={sha256_of(OUTPUT_PATH)}")


if __name__ == "__main__":
    main()
