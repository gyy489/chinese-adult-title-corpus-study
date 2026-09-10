"""Build a checksum manifest for everything under data/interim/01_profile/.

docs/research_protocol.md section 5 requires every formal run to record:
date, code version, input checksum, parameters, output path, output row
count, output checksum, and exceptions. The project has no git history to
pin a "code version" to (this directory is not a git repo), so each
producing script's own SHA-256 is recorded instead as its version fingerprint.

This script only reads data/raw/ and data/interim/01_profile/; it writes
exactly one file: data/interim/01_profile/manifest.json.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROFILE_DIR = PROJECT_ROOT / "data" / "interim" / "01_profile"
RAW_PATH = PROJECT_ROOT / "data" / "raw" / "titles.jsonl"

# path (relative to PROJECT_ROOT) -> producing script (relative to PROJECT_ROOT)
PRODUCED_BY = {
    "data/interim/01_profile/raw_profile.json": "src/preprocessing/profile_raw.py",
    "data/interim/01_profile/raw_profile_report.md": "src/preprocessing/profile_raw.py",
    "data/interim/01_profile/bracket_contents.tsv": "src/preprocessing/extract_bracket_content.py",
    "data/interim/01_profile/bracket_contents_summary.md": "src/preprocessing/extract_bracket_content.py",
    "data/interim/01_profile/bracket_contents_only.txt": "src/preprocessing/draft_bracket_coding.py",
    "data/interim/01_profile/bracket_content_coding_draft.csv": "src/preprocessing/draft_bracket_coding.py",
    "data/interim/01_profile/regional_origin_for_review.csv": "src/preprocessing/export_review_tables.py",
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
        "generated_by": "src/preprocessing/build_profile_manifest.py",
        "note": (
            "This directory (data/interim/01_profile) is read-only audit "
            "output derived from data/raw/titles.jsonl. No file here has "
            "been used to overwrite or modify data/raw/. "
            "'producing_script_sha256' substitutes for a git commit hash "
            "as the code-version fingerprint, since this project is not a "
            "git repository."
        ),
        "input_snapshot": {
            "path": "data/raw/titles.jsonl",
            "sha256": raw_sha256,
        },
        "outputs": entries,
    }

    out_path = PROFILE_DIR / "manifest.json"
    out_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"wrote {out_path.relative_to(PROJECT_ROOT)}")
    for e in entries:
        print(
            f"  {e['output_path']}  sha256={e['output_sha256']}  "
            f"lines={e['output_line_count']}  bytes={e['output_bytes']}"
        )


if __name__ == "__main__":
    main()
