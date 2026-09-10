"""Apply reviewed exact-span replacements and deduplicate remediated text."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


class RemediationError(ValueError):
    """Raised when reviewed spans do not match or overlap."""


@dataclass(frozen=True)
class SpanReplacement:
    start: int
    end: int
    expected: str
    replacement: str
    category: str


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def apply_exact_spans(text: str, replacements: Iterable[SpanReplacement]) -> str:
    """Replace reviewed spans from right to left after strict validation."""

    ordered = sorted(replacements, key=lambda item: (item.start, item.end))
    prior_end = 0
    for item in ordered:
        if not (0 <= item.start < item.end <= len(text)):
            raise RemediationError(f"span outside text: {item.start}:{item.end}")
        if item.start < prior_end:
            raise RemediationError("reviewed spans overlap")
        if text[item.start : item.end] != item.expected:
            raise RemediationError("reviewed span does not match expected text")
        prior_end = item.end
    result = text
    for item in reversed(ordered):
        result = result[: item.start] + item.replacement + result[item.end :]
    return result


def remediate_records(
    records: Iterable[dict[str, Any]],
    decisions: dict[str, list[SpanReplacement]],
    *,
    id_field: str = "record_id",
    text_field: str = "final_text",
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Apply decisions and reapply exact-final-text deduplication."""

    output: list[dict[str, Any]] = []
    changed = 0
    replacements_applied = 0
    for row in records:
        record_id = str(row[id_field])
        spans = decisions.get(record_id, [])
        source_text = str(row[text_field])
        final_text = apply_exact_spans(source_text, spans) if spans else source_text
        changed += int(final_text != source_text)
        replacements_applied += len(spans)
        output.append(
            {
                **row,
                text_field: final_text,
                "final_text_sha256": text_sha256(final_text),
                "privacy_remediated": bool(spans),
            }
        )
    kept: list[dict[str, Any]] = []
    seen: set[str] = set()
    removed = 0
    for row in sorted(output, key=lambda item: (item["final_text_sha256"], str(item[id_field]))):
        key = str(row["final_text_sha256"])
        if key in seen:
            removed += 1
            continue
        seen.add(key)
        kept.append(row)
    return kept, {
        "input_records": len(output),
        "records_changed": changed,
        "exact_span_replacements": replacements_applied,
        "post_remediation_duplicates_removed": removed,
        "output_unique_texts": len(kept),
    }
