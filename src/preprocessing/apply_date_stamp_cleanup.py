"""Rule 013: strip release/freshness date stamps from free text.

User asked whether "2025年9月" style markers count as pollution too, and
specifically about bare "9月" forms. Investigated both before writing this
rule (see docs/cleaning_rules.md rule 013):

- "YYYY年MM月[DD日]" as a title prefix is
  unambiguous: 129 records, always functioning as a release/freshness date
  stamp, never part of a narrative sentence. Safe to strip whenever it
  appears (not just at the very start, since brand/code stripping upstream
  can shift what is now "the start" of the string).
- Bare "N月" (no year) turned out to be genuinely ambiguous. Most matches
  are release-stamp compounds, but a real minority are load-bearing timeline
  content in news-like narratives. Blanket-stripping bare month numbers would
  damage exactly the kind of "吃瓜/news narrative" content the user has
  separately retained as analytically meaningful.
  This rule therefore only strips bare "N月" when immediately adjacent to
  one of a small set of unambiguous release-marker words (新作/作品/流出/
  视频/直播/首曝/剧作/写真/新曝/付费/定制) -- 21 records -- and leaves all
  other bare "N月" occurrences untouched.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

try:
    from .extract_bracket_content import sha256_of
except ImportError:  # Direct script execution.
    from extract_bracket_content import sha256_of

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_PATH = PROJECT_ROOT / "data" / "raw" / "titles.jsonl"
MANIFEST_PATH = PROJECT_ROOT / "data" / "raw" / "manifest.json"
NORMALIZED_PATH = PROJECT_ROOT / "data" / "interim" / "02_normalized" / "titles_bracket_cleaned.jsonl"

FULL_DATE_RE = re.compile(r"\d{4}年\d{1,2}月(\d{1,2}日)?[，,、]?\s*")

RELEASE_WORDS = ["新作", "作品", "流出", "视频", "直播", "首曝", "剧作", "写真", "新曝", "付费", "定制"]
MONTH_PLUS_RELEASE_RE = re.compile(r"\d{1,2}月(?=(" + "|".join(RELEASE_WORDS) + r"))")

LEADING_EDGE_RE = re.compile(r"^[\-_·丨|>：:.。!！?？,，\s]+")
TRAILING_EDGE_RE = re.compile(r"[\-_·丨|>：:\s]+$")
WHITESPACE_RE = re.compile(r"\s+")


def clean_date_stamps(text: str) -> str:
    text = FULL_DATE_RE.sub("", text)
    text = MONTH_PLUS_RELEASE_RE.sub("", text)
    text = WHITESPACE_RE.sub(" ", text).strip()
    prev = None
    while prev != text:
        prev = text
        text = LEADING_EDGE_RE.sub("", text)
        text = TRAILING_EDGE_RE.sub("", text)
        text = text.strip()
    return text


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

    n_changed = 0
    n_became_empty = 0
    out_rows = []
    for rec in rows:
        before = rec["cleaned_title"]
        after = clean_date_stamps(before)
        if after != before:
            n_changed += 1
        if before.strip() and not after:
            n_became_empty += 1
        out_rows.append(
            {
                **rec,
                "cleaned_title": after,
                "rule_id": rec["rule_id"] + "+date_stamp_cleanup",
                "rule_versions": {
                    **rec.get("rule_versions", {}),
                    "date_stamp_cleanup": "v1.0-2026-07-31",
                },
            }
        )

    with NORMALIZED_PATH.open("w", encoding="utf-8") as f:
        for rec in out_rows:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"input_sha256={raw_sha256}")
    print(f"total_records={len(out_rows)}")
    print(f"records_changed={n_changed}")
    print(f"records_became_empty={n_became_empty}")
    print(f"output={NORMALIZED_PATH.relative_to(PROJECT_ROOT)} sha256={sha256_of(NORMALIZED_PATH)}")


if __name__ == "__main__":
    main()
