"""Checksum manifest for data/interim/05_tokenized/."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOKENIZED_DIR = PROJECT_ROOT / "data" / "interim" / "05_tokenized"
RAW_PATH = PROJECT_ROOT / "data" / "raw" / "titles.jsonl"

PRODUCED_BY = {
    "data/interim/05_tokenized/custom_dict.txt": "src/preprocessing/build_custom_dict.py",
    "data/interim/05_tokenized/custom_dict_report.md": "src/preprocessing/build_custom_dict.py",
    "data/interim/05_tokenized/series_names_reference.txt": "src/preprocessing/build_custom_dict.py",
    "data/interim/05_tokenized/sample_char.jsonl": "src/preprocessing/compare_tokenizers.py",
    "data/interim/05_tokenized/sample_jieba_default.jsonl": "src/preprocessing/compare_tokenizers.py",
    "data/interim/05_tokenized/sample_jieba_custom.jsonl": "src/preprocessing/compare_tokenizers.py",
    "data/interim/05_tokenized/sample_thulac.jsonl": "src/preprocessing/compare_tokenizers.py",
    "data/interim/05_tokenized/sample_side_by_side.txt": "src/preprocessing/compare_tokenizers.py",
    "data/interim/05_tokenized/comparison_summary.json": "src/preprocessing/compare_tokenizers.py",
    "data/interim/05_tokenized/titles_tokenized.jsonl": "src/preprocessing/tokenize_full_corpus.py",
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
            }
        )

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_by": "src/preprocessing/build_tokenized_manifest.py",
        "note": (
            "Tokenizer comparison artifacts (docs/tokenization_comparison.md "
            "is the human-readable report). custom_dict.txt is the "
            "recommended jieba user dictionary. series_names_reference.txt "
            "is a superseded historical artifact and is not used by current "
            "tokenization or candidate generation. titles_tokenized.jsonl is "
            "the full-corpus tokenization of all 24,233 rule-015-cleaned, "
            "rule-016-excluded records (section 9 of the report; row count "
            "dropped from 24,494 to 24,245 on 2026-08-02 when rule 016 v1.0 "
            "excluded 249 foreign-performer titles, then to 24,233 the same "
            "day when rule 016 v1.1 added the known_foreign_code_prefix and "
            "Pussy Hunter brand signals (12 more records), see "
            "docs/cleaning_rules.md rule 016) -- the sample_*.jsonl files "
            "above are only the earlier 300-record scheme-comparison sample."
        ),
        "input_snapshot": {"path": "data/raw/titles.jsonl", "sha256": raw_sha256},
        "outputs": entries,
    }

    out_path = TOKENIZED_DIR / "manifest.json"
    out_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out_path.relative_to(PROJECT_ROOT)}")
    for e in entries:
        print(f"  {e['output_path']}  sha256={e['output_sha256']}")


if __name__ == "__main__":
    main()
