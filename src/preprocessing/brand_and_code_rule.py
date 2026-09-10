"""Rule 002: remove production labels and catalogue codes from title text.

The structural regular expressions below are the production implementation.
Two closed literal lists (verified brand strings and lowercase catalogue-code
prefixes) are restricted because they can reveal source provenance. A private
rerun can provide them in a JSON file through ``TITLE_STUDY_RESTRICTED_RULES``;
the public default is deliberately empty.

Expected private JSON shape::

    {"brand_terms": ["..."], "lowercase_code_prefixes": ["..."]}
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path


def _load_restricted_literals() -> tuple[list[str], list[str]]:
    configured = os.environ.get("TITLE_STUDY_RESTRICTED_RULES", "").strip()
    if not configured:
        return [], []
    payload = json.loads(Path(configured).read_text(encoding="utf-8"))
    brand_terms = payload.get("brand_terms", [])
    lowercase_prefixes = payload.get("lowercase_code_prefixes", [])
    if not isinstance(brand_terms, list) or not all(
        isinstance(value, str) and value for value in brand_terms
    ):
        raise ValueError("brand_terms must be a list of non-empty strings")
    if not isinstance(lowercase_prefixes, list) or not all(
        isinstance(value, str) and value.islower() and value.isalpha()
        for value in lowercase_prefixes
    ):
        raise ValueError(
            "lowercase_code_prefixes must be lowercase alphabetic strings"
        )
    return brand_terms, lowercase_prefixes


KNOWN_BRAND_TERMS, LOWERCASE_CODE_PREFIXES = _load_restricted_literals()

# Proper-noun-like run immediately followed by an uppercase catalogue code.
# The EP guard prevents an episode marker from swallowing its preceding title.
BRAND_OR_PERFORMER_CODE_RE = re.compile(
    r"[一-鿿A-Za-z]{2,10}(?!EP\d)[A-Z]{2,8}-?\d{2,5}(?:-\d{1,2})?"
)

# Standalone uppercase catalogue-code shape.
BARE_CODE_RE = re.compile(
    r"(?<![A-Za-z0-9])[A-Z]{2,8}-?\d{2,5}(?:-\d{1,2})?(?![A-Za-z0-9])"
)

# Same code shape with a short leading digit run.
DIGIT_PREFIXED_CODE_RE = re.compile(
    r"(?<![A-Za-z0-9])\d{1,3}[A-Z]{2,8}-?\d{2,5}[A-Z]?(?:-\d{1,2})?"
    r"(?![A-Za-z0-9])"
)

LOWERCASE_CODE_PREFIX_RE = (
    re.compile(
        r"(?<![A-Za-z0-9])(?:"
        + "|".join(re.escape(value) for value in LOWERCASE_CODE_PREFIXES)
        + r")-?\d{2,4}(?![A-Za-z0-9])"
    )
    if LOWERCASE_CODE_PREFIXES
    else None
)


def find_brand_and_code_spans(text: str) -> list[tuple[int, int, str, str]]:
    """Return sorted, non-overlapping spans selected by production precedence."""

    spans: list[tuple[int, int, str, str]] = []
    for term in KNOWN_BRAND_TERMS:
        for match in re.finditer(re.escape(term), text, flags=re.IGNORECASE):
            spans.append(
                (
                    match.start(),
                    match.end(),
                    text[match.start() : match.end()],
                    "known_brand_term",
                )
            )
    for match in BRAND_OR_PERFORMER_CODE_RE.finditer(text):
        spans.append(
            (
                match.start(), match.end(), match.group(0),
                "brand_or_performer_code_pattern",
            )
        )
    for match in BARE_CODE_RE.finditer(text):
        spans.append(
            (match.start(), match.end(), match.group(0), "bare_code_pattern")
        )
    for match in DIGIT_PREFIXED_CODE_RE.finditer(text):
        spans.append(
            (
                match.start(), match.end(), match.group(0),
                "digit_prefixed_code_pattern",
            )
        )
    if LOWERCASE_CODE_PREFIX_RE is not None:
        for match in LOWERCASE_CODE_PREFIX_RE.finditer(text):
            spans.append(
                (
                    match.start(), match.end(), match.group(0),
                    "lowercase_code_prefix_pattern",
                )
            )

    spans.sort(key=lambda span: (span[0], -(span[1] - span[0])))
    merged: list[tuple[int, int, str, str]] = []
    for span in spans:
        if merged and span[0] < merged[-1][1]:
            continue
        merged.append(span)
    return merged
