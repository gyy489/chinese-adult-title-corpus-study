"""Full-corpus tokenization of the round1+round2 combined, deidentified dataset.

This supersedes `tokenize_full_corpus.py` (which only ever covered the
round1-only, pre-deidentification 24,144-row corpus at
`04_organizer_prefix_cleaned/titles_for_analysis.csv`; that output and script
are left untouched as a historical record, not deleted or overwritten).

Deliberate naming choice: this script's output field named `cleaned_title`
holds `deidentified_title` from the input, not `cleaned_title`. Everything
downstream that reads a tokenized corpus (`apply_codebook_candidates.py`,
`src/review_app/store.py`) expects a `cleaned_title` key and is otherwise
input-path-parameterized, so reusing that key lets those scripts run against
this new corpus completely unmodified. The alternative -- threading a new
field name through both of those -- would touch the review app's SQLite
schema and its frontend, for a codebase-wide rename whose only benefit is a
label. The `original_title` and `pre_deidentification_cleaned_title` fields
are carried through unchanged so the substitution is always traceable back to
what a reader would otherwise expect "cleaned_title" to mean at earlier
pipeline stages. See docs/data_pipeline_methods.md for the full rationale:
tokenizing (and therefore any downstream word-frequency table, codebook
example, or quoted excerpt) the deidentified text rather than the raw
cleaned text is what makes deidentification actually protect privacy in the
analysis and paper, rather than being upstream decoration that a later stage
silently bypasses.

Same tokenizer scheme as `tokenize_full_corpus.py` (jieba default dictionary
+ custom_dict.txt, selected in docs/tokenization_comparison.md). The custom
dictionary itself is shared with `tokenize_full_corpus.py`'s round1-only
pipeline (2026-08-04/05 additions apply to both), but this script alone also
force-adds every `KNOWN_RECURRING_ALIASES` entry from
`detect_deidentification_candidates.py` at a very high frequency (2026-08-05,
see docs/tokenization_comparison.md section 10): real recurring performer
names otherwise get fragmented by jieba's dynamic-programming path selection
whenever a substring collides with an unrelated dictionary word. This was
corpus-measured at a 24% fragmentation rate across the confirmed
name list before this fix, 99%+ after. This is deliberately NOT threaded into
`tokenize_full_corpus.py`/round1-only's shared `custom_dict.txt`, since it
would force a resync of round1-only's 05/07/08 layers for a fix whose
motivation (protecting/preserving already-deidentified or still-residual
person names) doesn't apply there the same way. Whitespace-only tokens are
dropped, matching the established QC finding in tokenize_full_corpus.py.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import jieba
try:
    from .extract_bracket_content import sha256_of
except ImportError:  # Direct script execution.
    from extract_bracket_content import sha256_of

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INPUT_PATH = PROJECT_ROOT / "data" / "interim" / "11_deidentification" / "titles_for_analysis_deidentified.csv"
CUSTOM_DICT_PATH = PROJECT_ROOT / "data" / "interim" / "05_tokenized" / "custom_dict.txt"
OUTPUT_DIR = PROJECT_ROOT / "data" / "interim" / "12_tokenized"
OUTPUT_PATH = OUTPUT_DIR / "titles_tokenized.jsonl"
MANIFEST_PATH = OUTPUT_DIR / "manifest.json"

sys.path.insert(0, str(PROJECT_ROOT))
from src.preprocessing.detect_deidentification_candidates import KNOWN_RECURRING_ALIASES  # noqa: E402

# Frequency chosen empirically (2026-08-05): high enough to reliably win
# jieba's dynamic-programming path selection against competing dictionary
# words, verified against all corpus-confirmed real names (99%+ isolated as
# one token, up from 76% before). Also used for the general-vocabulary
# custom_dict.txt additions from the same round, for consistency.
FORCED_WORD_FREQUENCY = 1_000_000


def build_jieba_custom() -> jieba.Tokenizer:
    jb = jieba.Tokenizer()
    jb.initialize()
    with CUSTOM_DICT_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) >= 2:
                jb.add_word(parts[0], freq=int(parts[1]))
    for name in KNOWN_RECURRING_ALIASES:
        jb.add_word(name, freq=FORCED_WORD_FREQUENCY, tag="nr")
    return jb


def main() -> None:
    with INPUT_PATH.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    jb = build_jieba_custom()

    n_empty = 0
    token_counts: list[int] = []
    all_tokens: list[str] = []
    out_rows: list[dict[str, object]] = []
    for row in rows:
        text = row["deidentified_title"]
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
                "data_round": row.get("data_round", ""),
                "original_title": row["original_title"],
                "pre_deidentification_cleaned_title": row["cleaned_title"],
                "cleaned_title": text,
                "deidentification_applied": row.get("deidentification_applied", "").strip().lower() == "true",
                "deidentification_replacement_count": row.get("deidentification_replacement_count", "0"),
                "tokens": tokens,
                "token_count": len(tokens),
                "is_empty_after_cleaning": not bool(text.strip()),
                "tokenizer": "jieba_default_dict+custom_dict",
                "custom_dict_sha256": sha256_of(CUSTOM_DICT_PATH),
            }
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        for r in out_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    single_char = sum(1 for t in all_tokens if len(t) == 1)
    non_empty_counts = [c for c, r in zip(token_counts, out_rows) if not r["is_empty_after_cleaning"]]
    stats = {
        "input_sha256": sha256_of(INPUT_PATH),
        "total_records": len(out_rows),
        "empty_after_cleaning": n_empty,
        "avg_tokens_per_title_non_empty": sum(non_empty_counts) / len(non_empty_counts),
        "avg_token_length": sum(len(t) for t in all_tokens) / len(all_tokens),
        "single_char_token_ratio": single_char / len(all_tokens),
        "unique_token_types": len(set(all_tokens)),
    }
    for key, value in stats.items():
        print(f"{key}={value}")
    print(f"output={OUTPUT_PATH.relative_to(PROJECT_ROOT)} sha256={sha256_of(OUTPUT_PATH)}")

    manifest = {
        "generated_by": "src/preprocessing/tokenize_deidentified_corpus.py",
        "note": (
            "Tokenizes deidentified_title from the round1+round2 combined, deidentified "
            "corpus (superset of tokenize_full_corpus.py, which only ever covered the "
            "round1-only, pre-deidentification 24,144-row corpus). The cleaned_title field "
            "in this output holds deidentified text, not pre-deidentification cleaned text "
            "-- see this script's module docstring."
        ),
        "input_path": str(INPUT_PATH.relative_to(PROJECT_ROOT)),
        "input_sha256": stats["input_sha256"],
        "input_rows": len(rows),
        "custom_dict_path": str(CUSTOM_DICT_PATH.relative_to(PROJECT_ROOT)),
        "custom_dict_sha256": sha256_of(CUSTOM_DICT_PATH),
        "custom_dict_note": (
            "250 entries as of 2026-08-05: the round1-only 195-word base "
            "(docs/tokenization_comparison.md section 9, 380-record manual QC) "
            "plus 55 general-vocabulary words added this round (docs/tokenization_"
            "comparison.md section 10) across two methods: (a) a systematic "
            "adjacent-single-char-token bigram frequency analysis (31 words), and "
            "(b) manual sampling + a systematic n-gram/top-token fragmentation-rate "
            "sweep that catches multi-char real words losing jieba's DP path "
            "competition to a stronger dictionary word or HMM new-word invention -- "
            "a failure mode the bigram method structurally cannot see (24 words, "
            "incl. 萝莉 at 30%->0% fragmentation across 3,392 occurrences, the "
            "single largest fix this round). These 55 ARE shared with "
            "tokenize_full_corpus.py/round1-only's custom_dict.txt."
        ),
        "forced_recurring_alias_words": len(KNOWN_RECURRING_ALIASES),
        "forced_recurring_alias_note": (
            "Every KNOWN_RECURRING_ALIASES entry from detect_deidentification_"
            "candidates.py is additionally force-added at "
            f"freq={FORCED_WORD_FREQUENCY} (nr) -- NOT written to custom_dict.txt, "
            "dynamically pulled from the single source of truth every run. "
            "combined pipeline only, see module docstring."
        ),
        "stats": stats,
        "output_path": str(OUTPUT_PATH.relative_to(PROJECT_ROOT)),
        "output_sha256": sha256_of(OUTPUT_PATH),
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
