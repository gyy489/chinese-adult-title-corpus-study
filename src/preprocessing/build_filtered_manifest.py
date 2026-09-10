"""Checksum manifest for data/interim/03_filtered/, mirroring the
01_profile/02_normalized manifest pattern."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FILTERED_DIR = PROJECT_ROOT / "data" / "interim" / "03_filtered"
RAW_PATH = PROJECT_ROOT / "data" / "raw" / "titles.jsonl"

PRODUCED_BY = {
    "data/interim/03_filtered/titles_filtered.jsonl": "src/preprocessing/filter_serial_story_posts.py",
    "data/interim/03_filtered/titles_all_for_review.csv": "src/preprocessing/export_review_csvs.py",
    "data/interim/03_filtered/titles_long_or_forum_flagged_for_review.csv": "src/preprocessing/export_review_csvs.py",
}


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def line_count(path: Path) -> int:
    with path.open("r", encoding="utf-8", errors="replace") as f:
        return sum(1 for _ in f)


def main() -> None:
    raw_sha256 = sha256_of(RAW_PATH)
    entries = []
    for rel_path, rel_script in sorted(PRODUCED_BY.items()):
        path = PROJECT_ROOT / rel_path
        script_path = PROJECT_ROOT / rel_script
        entries.append(
            {
                "output_path": rel_path,
                "output_sha256": sha256_of(path),
                "output_bytes": path.stat().st_size,
                "output_line_count": line_count(path),
                "produced_by_script": rel_script,
                "producing_script_sha256": sha256_of(script_path),
                "input_path": "data/raw/titles.jsonl",
                "input_sha256": raw_sha256,
            }
        )

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_by": "src/preprocessing/build_filtered_manifest.py",
        "note": (
            "Rule 003 v1.0 (serial forum-story post exclusion) was "
            "RETRACTED 2026-07-31 after user review found most excluded "
            "records were valid data; see filter_serial_story_posts.py "
            "module docstring and docs/cleaning_rules.md rule 003. All "
            "25,191 records are currently retained=true. "
            "titles_all_for_review.csv is the full current corpus; "
            "titles_long_or_forum_flagged_for_review.csv is a small, "
            "NOT-excluded flag list (title length > 200 chars or explicit "
            "forum-post metadata) kept for optional manual review."
        ),
        "input_snapshot": {"path": "data/raw/titles.jsonl", "sha256": raw_sha256},
        "outputs": entries,
    }

    out_path = FILTERED_DIR / "manifest.json"
    out_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out_path.relative_to(PROJECT_ROOT)}")
    for e in entries:
        print(f"  {e['output_path']}  sha256={e['output_sha256']}  lines={e['output_line_count']}")


if __name__ == "__main__":
    main()
