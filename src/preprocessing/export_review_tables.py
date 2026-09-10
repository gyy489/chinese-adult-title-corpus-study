"""Export focused review tables for a single category out of the bracket
content coding draft, so a human can review just one category at a time
without scrolling through all seven.

Reads data/interim/01_profile/bracket_content_coding_draft.csv (already
produced by draft_bracket_coding.py) and writes one CSV per requested
category into the same directory. Read-only with respect to data/raw/.
"""

from __future__ import annotations

import csv
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INPUT_CSV = PROJECT_ROOT / "data" / "interim" / "01_profile" / "bracket_content_coding_draft.csv"
OUTPUT_DIR = PROJECT_ROOT / "data" / "interim" / "01_profile"

CATEGORIES_TO_EXPORT = {
    "regional_origin": "regional_origin_for_review.csv",
}
# "undetermined" was exported here too until 2026-07-31, when the user
# reviewed undetermined_for_review.csv and decided it held too little
# useful information to be worth the review effort -- the file was deleted
# and this script no longer regenerates it. The 1,381 underlying bracket
# fragments remain excluded from content_descriptor by default (rule 001
# v1.3); see docs/cleaning_rules.md rule 001 for that decision.

OUTPUT_FIELDS = ["count", "content", "example_site", "example_title", "human_decision"]


def main() -> None:
    rows_by_category: dict[str, list[dict]] = {cat: [] for cat in CATEGORIES_TO_EXPORT}

    with INPUT_CSV.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cat = row["suggested_category"]
            if cat in rows_by_category:
                rows_by_category[cat].append(row)

    for cat, filename in CATEGORIES_TO_EXPORT.items():
        rows = sorted(rows_by_category[cat], key=lambda r: (-int(r["count"]), r["content"]))
        out_path = OUTPUT_DIR / filename
        with out_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
            writer.writeheader()
            for r in rows:
                writer.writerow(
                    {
                        "count": r["count"],
                        "content": r["content"],
                        "example_site": r["example_site"],
                        "example_title": r["example_title"],
                        "human_decision": "",
                    }
                )
        print(f"{cat}: {len(rows)} rows -> {out_path.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
