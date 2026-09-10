"""Apply the confirmed bracket-content rule to produce the first real cleaned
title-text layer.

Rule (docs/cleaning_rules.md, rule 001, v1.2 + the 2026-07-30 "default
exclude unless confirmed" decision): only bracket content classified as
content_descriptor (free-text content descriptors) is kept in the title text
used for linguistic analysis. Every other category -- structural_metadata,
creator_or_series_handle, marketing_boilerplate, regional_origin,
content_tag_4char, and undetermined -- is stripped by default because it has
not been confirmed as content-bearing.

Nothing is silently discarded: every removed span is recorded per-record in
`removed_spans`, and every kept span in `kept_spans`, so the transformation
is fully reversible and auditable per docs/research_protocol.md section 3.
`data/raw/titles.jsonl` is only read, never modified.

Out of scope (separate, still-undecided issues, see docs/research_log.md):
source_02 forum-post contamination, non-CJK titles, duplicate titles, HTML
entities. This script only executes the bracket-content rule.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

try:
    from .draft_bracket_coding import classify
    from .extract_bracket_content import BRACKET_PATTERNS, sha256_of
except ImportError:  # Direct script execution.
    from draft_bracket_coding import classify
    from extract_bracket_content import BRACKET_PATTERNS, sha256_of

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_PATH = PROJECT_ROOT / "data" / "raw" / "titles.jsonl"
MANIFEST_PATH = PROJECT_ROOT / "data" / "raw" / "manifest.json"
OUTPUT_DIR = PROJECT_ROOT / "data" / "interim" / "02_normalized"
OUTPUT_PATH = OUTPUT_DIR / "titles_bracket_cleaned.jsonl"

KEEP_CATEGORIES = {"content_descriptor"}
WHITESPACE_RE = re.compile(r"\s+")


def find_all_spans(title: str) -> list[tuple[int, int, str, str]]:
    spans = []
    for style, pattern in BRACKET_PATTERNS:
        for m in pattern.finditer(title):
            content = m.group(1).strip()
            if content == "":
                continue
            spans.append((m.start(0), m.end(0), style, content))
    spans.sort(key=lambda s: s[0])
    return spans


def resolve_nesting(
    spans: list[tuple[int, int, str, str]],
) -> tuple[list[tuple[int, int, str, str]], bool]:
    """Distinguish genuine bracket NESTING (e.g. 『a(b)c』, where the
    inner () span is fully contained in the outer 『』 span -- because
    the two use different delimiter characters, each bracket-type's regex
    matches independently and produces two spans for what is really one
    nested unit) from a genuine CROSSING overlap (partial overlap, no
    containment either way -- ambiguous, unsafe to auto-resolve).

    Returns (resolved_spans, had_crossing_overlap). When a crossing overlap
    is found, resolved_spans is empty and the caller should fall back to
    leaving the whole title untouched, same as before this fix.
    """
    resolved: list[tuple[int, int, str, str]] = []
    for span in spans:
        start, end = span[0], span[1]
        contained_in_existing = False
        crossing = False
        for r in resolved:
            r_start, r_end = r[0], r[1]
            if start >= r_start and end <= r_end:
                contained_in_existing = True
                break
            if start < r_end and end > r_start:
                crossing = True
                break
        if contained_in_existing:
            continue  # drop the inner span; the outer span's content covers it
        if crossing:
            return [], True
        resolved.append(span)
    resolved.sort(key=lambda s: s[0])
    return resolved, False


def clean_title(title: str) -> dict:
    raw_spans = find_all_spans(title)
    spans, crossing_overlap = resolve_nesting(raw_spans)
    if crossing_overlap:
        # Safety net for a genuinely ambiguous case (partial, non-nested
        # overlap) -- leave the title untouched rather than risk corrupting
        # text. Distinct from ordinary nesting, which is now resolved above.
        return {"cleaned_title": title, "kept_spans": [], "removed_spans": [], "skipped_due_to_overlap": True}

    pieces, kept, removed = [], [], []
    last_end = 0
    for start, end, style, content in spans:
        category, _matched_rule = classify(content)
        pieces.append(title[last_end:start])
        if category in KEEP_CATEGORIES:
            pieces.append(title[start:end])
            kept.append({"bracket_style": style, "content": content, "category": category})
        else:
            removed.append({"bracket_style": style, "content": content, "category": category})
        last_end = end
    pieces.append(title[last_end:])

    cleaned = WHITESPACE_RE.sub(" ", "".join(pieces)).strip()
    return {"cleaned_title": cleaned, "kept_spans": kept, "removed_spans": removed, "skipped_due_to_overlap": False}


def main() -> None:
    raw_sha256 = sha256_of(RAW_PATH)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if raw_sha256 != manifest["sha256"]:
        raise SystemExit(
            f"SHA-256 mismatch: raw file={raw_sha256} manifest={manifest['sha256']}. Refusing to run."
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    n_total = 0
    n_with_brackets = 0
    n_overlap_skipped = 0
    n_became_empty = 0
    removed_by_category: dict[str, int] = {}

    with RAW_PATH.open("r", encoding="utf-8") as fin, OUTPUT_PATH.open("w", encoding="utf-8") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            n_total += 1
            result = clean_title(rec["title"])

            if result["kept_spans"] or result["removed_spans"]:
                n_with_brackets += 1
            if result["skipped_due_to_overlap"]:
                n_overlap_skipped += 1
            for span in result["removed_spans"]:
                removed_by_category[span["category"]] = removed_by_category.get(span["category"], 0) + 1
            if rec["title"].strip() and not result["cleaned_title"]:
                n_became_empty += 1

            out_rec = {
                "record_id": rec["source_url"],
                "source_site": rec["source_site"],
                "original_title": rec["title"],
                "cleaned_title": result["cleaned_title"],
                "kept_spans": result["kept_spans"],
                "removed_spans": result["removed_spans"],
                "skipped_due_to_overlap": result["skipped_due_to_overlap"],
                "rule_id": "bracket_content_categorization",
                "rule_version": "v1.7+default-exclude-2026-08-03",
            }
            fout.write(json.dumps(out_rec, ensure_ascii=False) + "\n")

    print(f"input_sha256={raw_sha256}")
    print(f"total_records={n_total}")
    print(f"records_with_brackets={n_with_brackets}")
    print(f"records_skipped_due_to_overlap={n_overlap_skipped}")
    print(f"records_became_empty_after_cleaning={n_became_empty}")
    print("removed_span_counts_by_category:")
    for cat, cnt in sorted(removed_by_category.items(), key=lambda kv: -kv[1]):
        print(f"  {cat}: {cnt}")
    print(f"output={OUTPUT_PATH.relative_to(PROJECT_ROOT)} sha256={sha256_of(OUTPUT_PATH)}")


if __name__ == "__main__":
    main()
