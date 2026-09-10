"""Full-corpus tokenization of the official analysis dataset.

Runs the scheme selected in docs/tokenization_comparison.md (jieba, default
dictionary + custom_dict.txt) over the analysis dataset after rule 015
removes confirmed organizer-added grouping prefixes and immediate numeric
labels -- the 300-record comparison sample only established which scheme to
use, this is the first full-corpus run. The dataset was 24,494 records at
that time; rule 016 (2026-08-02, see docs/cleaning_rules.md) subsequently
excluded 249 foreign-performer titles, bringing it to 24,245.

Whitespace-only tokens (jieba segments literal space/full-width-space
characters as their own token) are dropped from the output -- confirmed via full-corpus QC that
43,488 such tokens were showing up across 14,485/24,494 records (at the time
of that QC pass, before rule 016), pure tokenization noise with no
linguistic content.

Historically (through the 2026-08-03 pipeline runs), a small number of
records had an empty cleaned_title here -- everything in the title was
classified as noise by upstream rules. In one audited pattern, descriptive
text was fused to a catalogue code inside a bracket, and rule 001 categorized an
entire bracket span as one unit, and a code-prefix glued directly onto real
content with no separator drags the whole span into "undetermined" ->
excluded. As of the rule 018 empty-title safety net (2026-08-04, see
src/preprocessing/empty_title_fallback.py and docs/cleaning_rules.md rule
018), the upstream `titles_for_analysis.csv` this script reads from no
longer contains any empty (or near-empty, <2 char) cleaned_title -- every
formerly-empty record was rescued (bracket content, or failing that the
full original_title) before reaching this script. `is_empty_after_cleaning`
is still computed and kept here as a defensive check (should always be 0
now), not removed, in case a future upstream change reintroduces the
problem; `empty_title_fallback_applied` / `empty_title_fallback_source` /
`cleaned_title_before_empty_title_fallback` are carried straight through
from the CSV so anyone reading this file can see which rows were rescued
and how.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import jieba
try:
    from .extract_bracket_content import sha256_of
except ImportError:  # Direct script execution.
    from extract_bracket_content import sha256_of

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_PATH = PROJECT_ROOT / "data" / "raw" / "titles.jsonl"
MANIFEST_PATH = PROJECT_ROOT / "data" / "raw" / "manifest.json"
ANALYSIS_PATH = PROJECT_ROOT / "data" / "interim" / "04_organizer_prefix_cleaned" / "titles_for_analysis.csv"
CUSTOM_DICT_PATH = PROJECT_ROOT / "data" / "interim" / "05_tokenized" / "custom_dict.txt"
OUTPUT_PATH = PROJECT_ROOT / "data" / "interim" / "05_tokenized" / "titles_tokenized.jsonl"


def build_jieba_custom() -> jieba.Tokenizer:
    jb = jieba.Tokenizer()
    jb.initialize()
    with CUSTOM_DICT_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) >= 2:
                jb.add_word(parts[0], freq=int(parts[1]))
    return jb


def main() -> None:
    raw_sha256 = sha256_of(RAW_PATH)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if raw_sha256 != manifest["sha256"]:
        raise SystemExit("SHA-256 mismatch against data/raw/manifest.json; refusing to run.")

    with ANALYSIS_PATH.open("r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    jb = build_jieba_custom()

    n_empty = 0
    token_counts = []
    all_tokens = []
    out_rows = []
    for row in rows:
        text = row["cleaned_title"]
        if not text.strip():
            n_empty += 1
            tokens: list[str] = []
        else:
            tokens = [t for t in jb.cut(text) if t.strip()]
        token_counts.append(len(tokens))
        all_tokens.extend(tokens)
        out_rows.append(
            {
                "record_id": row["record_id"],
                "source_site": row["source_site"],
                "original_title": row["original_title"],
                "cleaned_title_before_organizer_prefix_rule": row[
                    "cleaned_title_before_organizer_prefix_rule"
                ],
                "cleaned_title": text,
                "historical_prefix_label_v1_retracted": row[
                    "historical_prefix_label_v1_retracted"
                ],
                "organizer_prefix_removed": row["organizer_prefix_removed"],
                "organizer_number_removed": row["organizer_number_removed"],
                "organizer_prefix_rule_applied": row[
                    "organizer_prefix_rule_applied"
                ].lower()
                == "true",
                "independent_video_record": True,
                "cleaned_title_before_empty_title_fallback": row.get(
                    "cleaned_title_before_empty_title_fallback", ""
                ),
                "empty_title_fallback_applied": row.get(
                    "empty_title_fallback_applied", ""
                ).lower()
                == "true",
                "empty_title_fallback_source": row.get("empty_title_fallback_source", ""),
                "tokens": tokens,
                "token_count": len(tokens),
                "is_empty_after_cleaning": not bool(text.strip()),
                "tokenizer": "jieba_default_dict+custom_dict",
                "custom_dict_sha256": sha256_of(CUSTOM_DICT_PATH),
            }
        )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        for r in out_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    single_char = sum(1 for t in all_tokens if len(t) == 1)
    print(f"input_sha256={raw_sha256}")
    print(f"total_records={len(out_rows)}")
    print(f"empty_after_cleaning={n_empty}")
    non_empty_counts = [c for c, r in zip(token_counts, out_rows) if not r["is_empty_after_cleaning"]]
    print(f"avg_tokens_per_title(non_empty)={sum(non_empty_counts) / len(non_empty_counts):.2f}")
    print(f"avg_token_length={sum(len(t) for t in all_tokens) / len(all_tokens):.2f}")
    print(f"single_char_token_ratio={single_char / len(all_tokens):.3f}")
    print(f"unique_token_types={len(set(all_tokens))}")
    print(f"output={OUTPUT_PATH.relative_to(PROJECT_ROOT)} sha256={sha256_of(OUTPUT_PATH)}")


if __name__ == "__main__":
    main()
