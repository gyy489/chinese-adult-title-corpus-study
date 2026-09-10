"""Rule 014: strip trailing site-truncation ellipsis markers ("..").

Found by manually reading a 400-record stratified QC sample of
titles_for_analysis.csv: 388/24,494 records end in "-.." or " .." or "..".
Traced back to data/raw/titles.jsonl -- this is not something our own
cleaning introduced. It is a literal artifact in the raw scraped titles.
Two ASCII periods at the very end is a site-side truncation/
card-preview convention, not meaningful punctuation.

Scope is deliberately narrow and trailing-only:
- Only matches 2+ literal ASCII periods anchored at the end of the string
  (after whitespace-strip). Mid-title occurrences of ".." are common and
  are NOT touched -- manually checked 37 such records and every one uses
  ".." as a rhetorical/dramatic pause inside a sentence, not a truncation
  marker. Stripping those would damage genuine narrative content.
- The real Unicode ellipsis character "…" (as opposed to two ASCII periods)
  is also left untouched: 19 records end in it, and every one manually
  checked is a genuinely truncated raw title where the ellipsis is doing
  real work signaling “the source text is cut off here” -- removing the character
  would not recover any content, so there is nothing to gain by touching
  it. This is a documented, unresolved data-completeness limitation (see
  docs/data_pipeline_methods.md known issues), not something this rule
  fixes.

After stripping the trailing ".."  the leading/trailing connector-
punctuation trim loop (same set used by rules 002/012/013) is re-applied,
since removing the ellipsis can newly expose a connector character that was
attached to it (e.g. "...-精品力荐.." -> after rule 012 strips the
marketing term: "...-.." -> after this rule strips "..": "...-" -> trimmed
to "...").
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

TRAILING_TRUNCATION_RE = re.compile(r"\.{2,}\s*$")

LEADING_EDGE_RE = re.compile(r"^[\-_·丨|>：:.。!！?？,，\s]+")
TRAILING_EDGE_RE = re.compile(r"[\-_·丨|>：:\s]+$")
WHITESPACE_RE = re.compile(r"\s+")


def clean_truncation_marker(text: str) -> str:
    text = TRAILING_TRUNCATION_RE.sub("", text)
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
        after = clean_truncation_marker(before)
        if after != before:
            n_changed += 1
        if before.strip() and not after:
            n_became_empty += 1
        out_rows.append(
            {
                **rec,
                "cleaned_title": after,
                "rule_id": rec["rule_id"] + "+truncation_marker_cleanup",
                "rule_versions": {
                    **rec.get("rule_versions", {}),
                    "truncation_marker_cleanup": "v1.0-2026-07-31",
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
