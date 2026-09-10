"""Checksum manifest for data/interim/02_normalized/, mirroring the
data/interim/01_profile/manifest.json pattern. See that file's module
docstring for why the producing script's own SHA-256 stands in for a code
version (this project is not a git repository).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
NORMALIZED_DIR = PROJECT_ROOT / "data" / "interim" / "02_normalized"
RAW_PATH = PROJECT_ROOT / "data" / "raw" / "titles.jsonl"

PRODUCED_BY = {
    # Two-stage pipeline: clean_titles_bracket_content.py (rule 001) writes
    # this file first, then apply_brand_code_rule.py (rule 002) reads it
    # back in and overwrites it. The manifest records the *final* producing
    # script; run order is documented in docs/cleaning_rules.md and
    # docs/data_pipeline_methods.md.
    "data/interim/02_normalized/titles_bracket_cleaned.jsonl": "src/preprocessing/apply_brand_code_rule.py",
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
                "input_path": "data/raw/titles.jsonl",
                "input_sha256": raw_sha256,
            }
        )

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_by": "src/preprocessing/build_normalized_manifest.py",
        "note": (
            "data/interim/02_normalized applies confirmed cleaning rules to "
            "data/raw/titles.jsonl. data/raw/ is never modified. Rule "
            "provenance for each record is embedded in the output rows "
            "themselves (kept_spans/removed_spans/rule_id/rule_version), "
            "not only in this manifest."
        ),
        "input_snapshot": {"path": "data/raw/titles.jsonl", "sha256": raw_sha256},
        "outputs": entries,
    }

    out_path = NORMALIZED_DIR / "manifest.json"
    out_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out_path.relative_to(PROJECT_ROOT)}")
    for e in entries:
        print(f"  {e['output_path']}  sha256={e['output_sha256']}  lines={e['output_line_count']}")


if __name__ == "__main__":
    main()
