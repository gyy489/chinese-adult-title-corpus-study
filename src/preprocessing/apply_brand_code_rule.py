"""Apply rule 002 (brand/performer-code stripping) on top of rule 001's
output, and overwrite data/interim/02_normalized/titles_bracket_cleaned.jsonl
with the combined result.

Reads:
  - data/raw/titles.jsonl (for SHA-256 verification only)
  - data/interim/02_normalized/titles_bracket_cleaned.jsonl (rule 001 output,
    read as input to this stage before being overwritten)

Writes:
  - data/interim/02_normalized/titles_bracket_cleaned.jsonl (overwritten)

Each output row keeps the full per-step trail: `original_title` (untouched),
`title_after_bracket_rule` (rule 001 only), `cleaned_title` (rule 001 + rule
002, the final text for linguistic analysis), plus what rule 002 removed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

try:
    from .brand_and_code_rule import find_brand_and_code_spans
    from .extract_bracket_content import sha256_of
except ImportError:  # Direct script execution.
    from brand_and_code_rule import find_brand_and_code_spans
    from extract_bracket_content import sha256_of

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_PATH = PROJECT_ROOT / "data" / "raw" / "titles.jsonl"
MANIFEST_PATH = PROJECT_ROOT / "data" / "raw" / "manifest.json"
NORMALIZED_PATH = PROJECT_ROOT / "data" / "interim" / "02_normalized" / "titles_bracket_cleaned.jsonl"

WHITESPACE_RE = re.compile(r"\s+")


def strip_brand_and_code(text: str) -> tuple[str, list[dict]]:
    spans = find_brand_and_code_spans(text)
    pieces = []
    removed = []
    last_end = 0
    for start, end, matched, rule in spans:
        pieces.append(text[last_end:start])
        removed.append({"matched_text": matched, "matched_by": rule})
        last_end = end
    pieces.append(text[last_end:])
    cleaned = WHITESPACE_RE.sub(" ", "".join(pieces)).strip()
    # clean up leftover separator punctuation the brand/code prefix used to
    # attach to after a removed prefix.
    cleaned = re.sub(r"^[\-_·丨|>：: ]+", "", cleaned).strip()
    return cleaned, removed


def main() -> None:
    raw_sha256 = sha256_of(RAW_PATH)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if raw_sha256 != manifest["sha256"]:
        raise SystemExit("SHA-256 mismatch against data/raw/manifest.json; refusing to run.")

    rows = []
    with NORMALIZED_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    n_total = 0
    n_hit = 0
    n_became_empty = 0
    matched_rule_counts: dict[str, int] = {}

    out_rows = []
    for rec in rows:
        n_total += 1
        prior_cleaned = rec["cleaned_title"]
        final_cleaned, removed = strip_brand_and_code(prior_cleaned)
        if removed:
            n_hit += 1
        for r in removed:
            matched_rule_counts[r["matched_by"]] = matched_rule_counts.get(r["matched_by"], 0) + 1
        if prior_cleaned.strip() and not final_cleaned:
            n_became_empty += 1

        out_rows.append(
            {
                "record_id": rec["record_id"],
                "source_site": rec["source_site"],
                "original_title": rec["original_title"],
                "title_after_bracket_rule": prior_cleaned,
                "cleaned_title": final_cleaned,
                "kept_spans": rec["kept_spans"],
                "removed_spans": rec["removed_spans"],
                "removed_brand_code_spans": removed,
                "skipped_due_to_overlap": rec["skipped_due_to_overlap"],
                "rule_id": "bracket_content_categorization+brand_and_code_rule",
                "rule_versions": {
                    "bracket_content_categorization": "v1.7+default-exclude-2026-08-03",
                    "brand_and_code_rule": "v1.5-2026-08-04",
                },
            }
        )

    with NORMALIZED_PATH.open("w", encoding="utf-8") as f:
        for rec in out_rows:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"input_sha256={raw_sha256}")
    print(f"total_records={n_total}")
    print(f"records_with_brand_or_code_removed={n_hit}")
    print(f"records_became_empty_after_this_stage={n_became_empty}")
    print("matched_by_counts:")
    for rule, cnt in sorted(matched_rule_counts.items(), key=lambda kv: -kv[1]):
        print(f"  {rule}: {cnt}")
    print(f"output={NORMALIZED_PATH.relative_to(PROJECT_ROOT)} sha256={sha256_of(NORMALIZED_PATH)}")


if __name__ == "__main__":
    main()
