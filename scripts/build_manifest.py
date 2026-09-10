#!/usr/bin/env python3
"""Build or verify the deterministic public-artifact checksum manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "results" / "release_manifest.json"


def manifestable_files() -> list[Path]:
    files = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path == MANIFEST:
            continue
        relative = path.relative_to(ROOT)
        if ".git" in relative.parts or "__pycache__" in relative.parts:
            continue
        if path.suffix in {".pyc", ".pyo"} or path.name == ".DS_Store":
            continue
        files.append(path)
    return sorted(files)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def payload() -> dict[str, object]:
    files = []
    for path in manifestable_files():
        files.append(
            {
                "path": path.relative_to(ROOT).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    return {
        "release_id": "public-pipeline-v2.0.0",
        "scope": "Final public pipeline code, synthetic fixtures, stage manifests, and aggregate results; no real title-level data.",
        "files": files,
    }


def encoded() -> bytes:
    return (json.dumps(payload(), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = encoded()
    if args.check:
        if not MANIFEST.exists() or MANIFEST.read_bytes() != expected:
            raise SystemExit("results/release_manifest.json is missing or stale")
    else:
        MANIFEST.write_bytes(expected)


if __name__ == "__main__":
    main()
