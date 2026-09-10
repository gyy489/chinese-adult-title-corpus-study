"""Config-driven public implementation of the final preprocessing sequence.

The engine preserves source text and records every applied rule. The production
literal dictionaries and source-specific adapters are intentionally not
released; the public configuration uses synthetic examples with the same rule
interfaces.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable


class PreprocessingError(ValueError):
    """Raised when a rule or input record violates the public contract."""


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_rules(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("rules"), list):
        raise PreprocessingError("rule configuration must contain a rules array")
    return payload


def _collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\u3000", " ")).strip()


def apply_rule(text: str, rule: dict[str, Any]) -> tuple[str, int]:
    """Apply one declared transformation and return `(new_text, replacements)`."""

    kind = rule.get("kind")
    if kind == "unicode_normalize":
        form = str(rule.get("form", "NFKC"))
        updated = unicodedata.normalize(form, text)
        return updated, int(updated != text)
    if kind == "literal_replace":
        old = str(rule["old"])
        new = str(rule.get("new", ""))
        return text.replace(old, new), text.count(old)
    if kind == "regex_replace":
        flags = re.IGNORECASE if rule.get("ignore_case") else 0
        return re.subn(str(rule["pattern"]), str(rule.get("replacement", "")), text, flags=flags)
    if kind == "character_map":
        mapping = rule.get("mapping")
        if not isinstance(mapping, dict):
            raise PreprocessingError("character_map requires an object mapping")
        count = sum(text.count(str(source)) for source in mapping)
        return "".join(str(mapping.get(char, char)) for char in text), count
    if kind == "collapse_whitespace":
        updated = _collapse_whitespace(text)
        return updated, int(updated != text)
    raise PreprocessingError(f"unsupported rule kind: {kind!r}")


def semantic_cjk_length(text: str) -> int:
    """Count CJK ideographs after removing whitespace and punctuation."""

    count = 0
    for char in text:
        codepoint = ord(char)
        if (
            0x3400 <= codepoint <= 0x4DBF
            or 0x4E00 <= codepoint <= 0x9FFF
            or 0xF900 <= codepoint <= 0xFAFF
        ):
            count += 1
    return count


def transform_record(record: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Transform one record while retaining source text and an audit trail."""

    if not isinstance(record.get("title_text"), str):
        raise PreprocessingError("record.title_text must be a string")
    output = deepcopy(record)
    source_text = record["title_text"]
    current = source_text
    audit: list[dict[str, Any]] = []
    for rule in config["rules"]:
        updated, replacements = apply_rule(current, rule)
        if replacements:
            audit.append(
                {
                    "rule_id": str(rule["id"]),
                    "replacement_count": replacements,
                    "before_sha256": text_sha256(current),
                    "after_sha256": text_sha256(updated),
                }
            )
        current = updated
    length = semantic_cjk_length(current)
    minimum = int(config.get("minimum_semantic_cjk_length", 0))
    output.update(
        {
            "source_text": source_text,
            "final_text": current,
            "final_text_sha256": text_sha256(current),
            "semantic_cjk_length": length,
            "record_validity": "valid" if length >= minimum else "excluded_short",
            "transform_audit": audit,
        }
    )
    return output


def transform_records(
    records: Iterable[dict[str, Any]], config: dict[str, Any]
) -> list[dict[str, Any]]:
    return [transform_record(record, config) for record in records]


def flag_exact_source_duplicates(
    records: Iterable[dict[str, Any]],
    *,
    text_field: str = "title_text",
    id_field: str = "record_id",
    time_field: str = "crawl_time",
) -> list[dict[str, Any]]:
    """Retain all rows while flagging one deterministic canonical per exact text."""

    groups: dict[str, list[dict[str, Any]]] = {}
    for row in records:
        text = str(row[text_field])
        groups.setdefault(text, []).append(row)
    output: list[dict[str, Any]] = []
    for text in sorted(groups, key=text_sha256):
        group = sorted(
            groups[text],
            key=lambda row: (str(row.get(time_field, "")), str(row.get(id_field, ""))),
        )
        group_id = text_sha256(text)[:12]
        for index, row in enumerate(group):
            output.append(
                {
                    **row,
                    "duplicate_group_id": group_id,
                    "duplicate_group_size": len(group),
                    "is_duplicate_canonical": index == 0,
                }
            )
    return output


def deduplicate_final_text(
    records: Iterable[dict[str, Any]], *, valid_only: bool = True
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Keep the first deterministic contribution for each exact final text."""

    ordered = sorted(
        records,
        key=lambda row: (
            str(row.get("final_text_sha256", "")),
            str(row.get("record_id", "")),
        ),
    )
    kept: list[dict[str, Any]] = []
    seen: set[str] = set()
    excluded_invalid = 0
    removed_duplicates = 0
    for row in ordered:
        if valid_only and row.get("record_validity") != "valid":
            excluded_invalid += 1
            continue
        key = str(row["final_text_sha256"])
        if key in seen:
            removed_duplicates += 1
            continue
        seen.add(key)
        kept.append(row)
    return kept, {
        "input_records": len(ordered),
        "excluded_invalid": excluded_invalid,
        "removed_exact_final_text_duplicates": removed_duplicates,
        "output_unique_texts": len(kept),
    }
