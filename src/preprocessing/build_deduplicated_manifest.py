"""Checksum manifest for data/interim/04_deduplicated/."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEDUP_DIR = PROJECT_ROOT / "data" / "interim" / "04_deduplicated"
RAW_PATH = PROJECT_ROOT / "data" / "raw" / "titles.jsonl"

PRODUCED_BY = {
    # titles_deduplicated.jsonl is written by flag_exact_duplicates.py, then
    # updated in place by apply_length_forum_exclusion.py (rule 009),
    # apply_non_cjk_exclusion.py (rule 011), and
    # apply_foreign_performer_exclusion.py (rule 016), in that order.
    "data/interim/04_deduplicated/titles_deduplicated.jsonl": "src/preprocessing/apply_foreign_performer_exclusion.py",
    "data/interim/04_deduplicated/non_cjk_titles_for_review.csv": "src/preprocessing/export_review_and_analysis_views.py",
    "data/interim/04_deduplicated/duplicate_groups_for_review.csv": "src/preprocessing/export_review_and_analysis_views.py",
    "data/interim/04_deduplicated/foreign_performer_titles_for_review.csv": "src/preprocessing/export_review_and_analysis_views.py",
    "data/interim/04_deduplicated/titles_for_analysis.csv": "src/preprocessing/export_review_and_analysis_views.py",
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
        "generated_by": "src/preprocessing/build_deduplicated_manifest.py",
        "note": (
            "Rule 007: exact-duplicate flagging (identical original_title "
            "strings only, per the user's explicit definition). Nothing is "
            "removed -- all 25,191 rows are present with "
            "is_duplicate_canonical/duplicate_group_size/duplicate_group_id."
        ),
        "input_snapshot": {"path": "data/raw/titles.jsonl", "sha256": raw_sha256},
        "outputs": entries,
    }

    out_path = DEDUP_DIR / "manifest.json"
    out_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out_path.relative_to(PROJECT_ROOT)}")
    for e in entries:
        print(f"  {e['output_path']}  sha256={e['output_sha256']}  lines={e['output_line_count']}")


if __name__ == "__main__":
    main()
