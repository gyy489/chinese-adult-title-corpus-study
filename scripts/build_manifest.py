#!/usr/bin/env python3
"""Build or verify the deterministic public-artifact checksum manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "results" / "release_manifest.json"
PUBLIC_ROOTS = ("config", "prompts", "results/tables", "results/figures", "results/reproduced")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def payload() -> dict[str, object]:
    files = []
    for root_name in PUBLIC_ROOTS:
        for path in sorted((ROOT / root_name).rglob("*")):
            if path.is_file() and path != MANIFEST:
                files.append(
                    {
                        "path": path.relative_to(ROOT).as_posix(),
                        "bytes": path.stat().st_size,
                        "sha256": sha256(path),
                    }
                )
    return {
        "release_id": "public-results-v1.0.0",
        "scope": "Aggregate results and public annotation contracts only; no title-level data.",
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
