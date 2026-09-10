#!/usr/bin/env python3
"""Fail closed when a forbidden file, field, secret, or stale hash is present."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PARTS = {
    "data",
    "paper",
    "raw",
    "interim",
    "private",
    "sealed",
    "SC投稿情况",
}
FORBIDDEN_SUFFIXES = {
    ".db",
    ".docx",
    ".jsonl",
    ".pdf",
    ".sqlite",
    ".sqlite3",
    ".xlsx",
}
FORBIDDEN_CSV_FIELDS = {
    "title",
    "title_text",
    "final_title",
    "title_sha256",
    "source_title_sha256",
    "record_id",
    "record_key",
    "record_key_sha256",
    "source_site",
    "source_url",
    "url",
    "domain",
    "evidence",
    "evidence_span",
    "span",
}
SECRET_PATTERNS = {
    "OpenAI-style secret": re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    "GitHub token": re.compile(r"\b(?:ghp_|github_pat_)[A-Za-z0-9_]{12,}\b"),
    "AWS access key": re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "assigned API key": re.compile(r"(?i)\b(?:api_key|token|password)\s*[:=]\s*['\"][^'\"]+"),
    "local absolute path": re.compile(r"/(?:Users|home)/[^/\s]+/"),
    "submission identifier": re.compile(r"\bSECU-D-\d{2}-\d+\b"),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def repository_files() -> list[Path]:
    return [path for path in ROOT.rglob("*") if path.is_file() and ".git" not in path.parts]


def verify_paths(files: list[Path]) -> list[str]:
    errors = []
    for path in files:
        relative = path.relative_to(ROOT)
        if FORBIDDEN_PARTS.intersection(relative.parts):
            errors.append(f"forbidden path: {relative}")
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            errors.append(f"forbidden file type: {relative}")
        if path.stat().st_size > 1_000_000:
            errors.append(f"file exceeds 1 MB public-release limit: {relative}")
    return errors


def verify_csv_headers(files: list[Path]) -> list[str]:
    errors = []
    for path in files:
        if path.suffix.lower() != ".csv":
            continue
        with path.open(encoding="utf-8", newline="") as handle:
            fields = csv.DictReader(handle).fieldnames or []
        forbidden = FORBIDDEN_CSV_FIELDS.intersection(fields)
        if forbidden:
            errors.append(
                f"forbidden row-level fields in {path.relative_to(ROOT)}: {sorted(forbidden)}"
            )
    return errors


def verify_text(files: list[Path]) -> list[str]:
    errors = []
    for path in files:
        if path.suffix.lower() not in {".cff", ".csv", ".json", ".md", ".py", ".toml", ".yml", ".yaml"}:
            continue
        text = path.read_text(encoding="utf-8")
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                errors.append(f"{label} pattern in {path.relative_to(ROOT)}")
    return errors


def verify_manifest() -> list[str]:
    path = ROOT / "results" / "release_manifest.json"
    if not path.exists():
        return ["missing results/release_manifest.json"]
    payload = json.loads(path.read_text(encoding="utf-8"))
    errors = []
    for entry in payload.get("files", []):
        artifact = ROOT / entry["path"]
        if not artifact.is_file():
            errors.append(f"manifest file missing: {entry['path']}")
            continue
        if artifact.stat().st_size != entry["bytes"] or sha256(artifact) != entry["sha256"]:
            errors.append(f"manifest mismatch: {entry['path']}")
    return errors


def main() -> None:
    files = repository_files()
    errors = verify_paths(files) + verify_csv_headers(files) + verify_text(files) + verify_manifest()
    if errors:
        raise SystemExit("public release verification failed:\n- " + "\n- ".join(errors))
    print(f"public release verification passed: {len(files)} files checked")


if __name__ == "__main__":
    main()
