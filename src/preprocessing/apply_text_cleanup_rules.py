"""Rules 004-006: three small, independently low-risk fixes identified but
left unaddressed since the original raw-data audit. Applied on top of the
02_normalized output (rules 001+002) and written back to the same file.

Rule 004 -- strip the non-bracket promotional prefix "重磅福利>". Validated
against data/raw/titles.jsonl: exactly one prefix string accounts for all
449 hits of the "{word}>" pattern (no other variants exist), so this is a
single hard-coded string match, not a generalized rule.

Rule 005 -- unescape residual HTML entities (&amp; etc.) left over from
scraping. Only 5 records affected; python's html.unescape() handles the
general case.

Rule 006 -- NOT a text transformation. Adds an informational `contains_cjk`
flag (51 records are false) so the "Chinese title corpus" boundary claim in
docs/data_pipeline_methods.md can be stated precisely. Nothing is excluded;
this only flags, consistent with the lesson from rule 003's retraction
(default to flagging over excluding until a decision is confirmed).
"""

from __future__ import annotations

import html
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

PROMOTIONAL_PREFIX = "重磅福利>"
CJK_RE = re.compile(r"[一-鿿]")
WHITESPACE_RE = re.compile(r"\s+")


def apply_rule_004(text: str) -> tuple[str, bool]:
    if text.startswith(PROMOTIONAL_PREFIX):
        return text[len(PROMOTIONAL_PREFIX):].strip(), True
    return text, False


def apply_rule_005(text: str) -> tuple[str, bool]:
    unescaped = html.unescape(text)
    return unescaped, unescaped != text


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

    n_rule_004 = 0
    n_rule_005 = 0
    n_no_cjk = 0

    out_rows = []
    for rec in rows:
        text = rec["cleaned_title"]
        text, hit_004 = apply_rule_004(text)
        text, hit_005 = apply_rule_005(text)
        text = WHITESPACE_RE.sub(" ", text).strip()

        if hit_004:
            n_rule_004 += 1
        if hit_005:
            n_rule_005 += 1

        contains_cjk = bool(CJK_RE.search(rec["original_title"]))
        if not contains_cjk:
            n_no_cjk += 1

        out_rows.append(
            {
                **rec,
                "cleaned_title": text,
                "contains_cjk": contains_cjk,
                "rule_id": rec["rule_id"] + "+text_cleanup_rules_004_005",
                "rule_versions": {
                    **rec.get("rule_versions", {}),
                    "promotional_prefix_strip": "v1.0-2026-07-31",
                    "html_entity_unescape": "v1.0-2026-07-31",
                },
            }
        )

    with NORMALIZED_PATH.open("w", encoding="utf-8") as f:
        for rec in out_rows:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"input_sha256={raw_sha256}")
    print(f"total_records={len(out_rows)}")
    print(f"rule_004_promotional_prefix_stripped={n_rule_004}")
    print(f"rule_005_html_entities_unescaped={n_rule_005}")
    print(f"rule_006_contains_cjk_false={n_no_cjk} (flagged only, not excluded)")
    print(f"output={NORMALIZED_PATH.relative_to(PROJECT_ROOT)} sha256={sha256_of(NORMALIZED_PATH)}")


if __name__ == "__main__":
    main()
