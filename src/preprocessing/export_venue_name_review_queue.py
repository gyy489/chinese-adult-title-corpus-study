"""Export rows that mention a commercial-venue-type word for manual review.

Institution names (schools, hospitals, companies) can be caught safely by
suffix rules and a curated abbreviation dictionary because the space of real
institution names is closed and enumerable. Commercial venue names (a
specific billiards hall, KTV, beauty salon, etc.) are not: the same suffix
words appear overwhelmingly as generic scene-setting language ("情趣酒店",
"骚货酒店", "KTV包厢") rather than as a real, locatable business name (e.g.
"江苏东台名爵台球").

A 2026-08-04 diagnostic tried a GPE-anchored heuristic (only accept a venue
suffix match when a nearby NER location entity corroborates it) against a
300-row sample of the 776 rows containing a venue suffix in this corpus: it
fired 0 times, i.e. no safe automated signal was found that separates real
venue names from generic scene descriptions at useful recall. See
docs/deidentification_strategy.md and results/logs/deidentification/
2026-08-04-v2.9-abbreviation-and-scope-expansion.md.

Rather than guess, this script exports every row containing a venue-suffix
word as a manual review queue, mirroring the existing pilot_review_decision /
pilot_review_note pattern. It does not modify any title text.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = PROJECT_ROOT / "data" / "interim" / "10_combined_deduplicated" / "titles_for_analysis_combined.csv"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "interim" / "11_deidentification"
EXPORT_VERSION = "v1.0-venue-suffix-review-queue"

# Deliberately broad: false positives here only cost a reviewer a glance at
# one more row, unlike a false positive in an automated replacement rule.
VENUE_SUFFIX_TERMS = (
    "台球",
    "会所",
    "KTV",
    "网吧",
    "网咖",
    "洗浴",
    "美容院",
    "理发店",
    "车行",
    "工厂",
    "夜总会",
    "养生馆",
    "足浴",
)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def matched_terms(text: str) -> list[str]:
    return [term for term in VENUE_SUFFIX_TERMS if term in text]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    with args.input.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    queue_rows: list[dict[str, str]] = []
    for row in rows:
        text = row.get("cleaned_title", "") or ""
        terms = matched_terms(text)
        if not terms:
            continue
        queue_rows.append(
            {
                "record_id": row.get("record_id", ""),
                "source_site": row.get("source_site", ""),
                "data_round": row.get("data_round", ""),
                "matched_venue_terms": "|".join(terms),
                "cleaned_title": text,
                "review_decision": "",
                "review_note": "",
            }
        )

    output_path = output_dir / "venue_name_review_queue.csv"
    fieldnames = [
        "record_id",
        "source_site",
        "data_round",
        "matched_venue_terms",
        "cleaned_title",
        "review_decision",
        "review_note",
    ]
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(queue_rows)

    summary = {
        "status": "review_queue_only_no_text_modified",
        "export_version": EXPORT_VERSION,
        "input_path": str(args.input.relative_to(PROJECT_ROOT)),
        "input_sha256": sha256_file(args.input),
        "input_rows": len(rows),
        "venue_suffix_terms": list(VENUE_SUFFIX_TERMS),
        "queue_rows": len(queue_rows),
        "output_path": str(output_path.relative_to(PROJECT_ROOT)),
        "output_sha256": sha256_file(output_path),
    }
    summary_path = output_dir / "venue_name_review_queue_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
