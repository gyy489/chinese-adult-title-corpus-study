"""Cross-round exact-duplicate flagging for the round1+round2 combined
export (data/exports/2026-08-02_round1_round2_combined/titles.jsonl,
103,743 records across 10 sites).

This is the SAME grouping logic as round1's own rule 007
(`src/preprocessing/flag_exact_duplicates.py`) -- exact `original_title`
string match, one canonical record per group (earliest `crawl_time`,
`record_id` tiebreak), every record kept and flagged, nothing deleted --
just run over the wider round1+round2 pool instead of round1's own
25,191-record `03_filtered` layer. See that module's docstring for the
full rationale of the "only byte-identical strings count as duplicates"
definition; it is not re-derived here.

Two differences from the round1 script, both a direct consequence of
starting from the raw MySQL export instead of round1's already-cleaned
`03_filtered` layer (round1's cleaning-rule chain 001/002/004/005/006/008/
015 is deliberately NOT re-run here for round2 -- out of scope for this
pass, see docs/research_log.md 2026-08-02 "round1+round2合并去重" entry):

  1. `original_title` here is simply the MySQL `title` field, unmodified.
     `record_id` is the MySQL `source_url` field (matches round1's own
     `record_id` convention exactly, since round1's `record_id` was always
     `source_url` too -- see `filter_serial_story_posts.py`).
  2. `crawl_time` is read directly per-record from the export (every
     export row already carries its own `crawl_time`), instead of the
     round1 script's join back to `data/raw/titles.jsonl` by URL (which
     was only necessary because round1's `03_filtered` rows didn't carry
     `crawl_time` themselves).

Each output row also carries a `data_round` field ("round1"/"round2"),
determined by exact `source_url` membership in the frozen
`data/raw/titles.jsonl` set (round1's 4 sites and round2's 6 sites never
share a domain, so this is an unambiguous, collision-free split) -- purely
for convenience in the round2-focused summary report; it plays no role in
the grouping/canonical logic itself.

Invariant this script must preserve (verified separately by
scripts/tools outside this module, not asserted here): because every
round1 `crawl_time` (2026-05-08..2026-06-17) is strictly earlier than
every round2 `crawl_time` (2026-08-02), a duplicate group's earliest
member is always a round1 record whenever the group contains ANY round1
record. Sort order among round1 records themselves is determined solely
by their own (crawl_time, record_id) keys, unaffected by which round2
records (if any) also land in the same group. So every round1 record's
`is_duplicate_canonical` value computed here must be identical to what
`data/interim/04_deduplicated/titles_deduplicated.jsonl` already has
recorded for it. The only thing that changes for a round1 record is that
its `duplicate_group_size` can grow (if round2 reposted the identical
title text), and a round2 record whose title is byte-identical to an
existing round1 title correctly loses canonical status here -- something
round1's own isolated dedup run could not have detected.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXPORT_DIR = PROJECT_ROOT / "data" / "exports" / "2026-08-02_round1_round2_combined"
EXPORT_PATH = EXPORT_DIR / "titles.jsonl"
EXPORT_MANIFEST_PATH = EXPORT_DIR / "manifest.json"
RAW_PATH = PROJECT_ROOT / "data" / "raw" / "titles.jsonl"
OUTPUT_DIR = PROJECT_ROOT / "data" / "interim" / "10_combined_deduplicated"
OUTPUT_PATH = OUTPUT_DIR / "titles_combined_deduplicated.jsonl"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def group_id_for(title: str) -> str:
    # Identical hash function to round1's flag_exact_duplicates.py, so a
    # duplicate_group_id computed here for a round1-only group is
    # byte-identical to the one round1 already recorded for that group
    # (the hash depends only on the title string, never on group
    # membership or record count).
    return hashlib.sha256(title.encode("utf-8")).hexdigest()[:12]


def main() -> None:
    export_sha256 = sha256_of(EXPORT_PATH)
    export_manifest = json.loads(EXPORT_MANIFEST_PATH.read_text(encoding="utf-8"))
    if export_sha256 != export_manifest["sha256"]:
        raise SystemExit(
            "SHA-256 mismatch against data/exports/2026-08-02_round1_round2_combined/"
            "manifest.json; refusing to run on a snapshot that doesn't match its own manifest."
        )

    round1_urls: set[str] = set()
    with RAW_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                round1_urls.add(json.loads(line)["source_url"])

    rows = []
    with EXPORT_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            rows.append(
                {
                    "record_id": rec["source_url"],
                    "source_site": rec["source_site"],
                    "original_title": rec["title"],
                    "listing_url": rec["listing_url"],
                    "crawl_time": rec["crawl_time"],
                    "data_round": "round1" if rec["source_url"] in round1_urls else "round2",
                }
            )

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
        ordered = sorted(group, key=lambda r: (r["crawl_time"], r["record_id"]))
        for i, rec in enumerate(ordered):
            out_rows.append(
                {
                    **rec,
                    "duplicate_group_id": gid,
                    "duplicate_group_size": group_size,
                    "is_duplicate_canonical": i == 0,
                }
            )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        for rec in out_rows:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    n_canonical = sum(1 for r in out_rows if r["is_duplicate_canonical"])
    print(f"input_sha256={export_sha256}")
    print(f"total_records={len(out_rows)}")
    print(f"duplicate_title_groups={n_duplicate_groups}")
    print(f"extra_rows_beyond_first_in_group={n_extra_rows}")
    print(f"canonical_rows (is_duplicate_canonical=true)={n_canonical}")
    print(f"output={OUTPUT_PATH.relative_to(PROJECT_ROOT)} sha256={sha256_of(OUTPUT_PATH)}")


if __name__ == "__main__":
    main()
