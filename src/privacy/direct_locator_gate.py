"""Conservative public gate for common direct locators in final text.

This is one release gate, not a proof of anonymity. Private high-recall entity
lexicons and the reviewed candidate evidence are intentionally absent.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from typing import Any


PATTERNS: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
    "web_url": re.compile(r"(?i)\b(?:https?://|www\.)\S+"),
    "domain": re.compile(r"(?i)\b(?:[A-Z0-9-]+\.)+(?:com|net|org|cn|tv|cc)\b"),
    "mainland_phone": re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)"),
    "mainland_id": re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
    "social_handle": re.compile(r"(?<!\w)@[A-Za-z0-9_]{4,32}\b"),
    "labelled_contact": re.compile(
        r"(?i)(?:contact|email|phone|wechat|telegram|qq)\s*[:：]\s*\S+"
    ),
}


def scan_text(text: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for category, pattern in PATTERNS.items():
        for match in pattern.finditer(text):
            findings.append(
                {
                    "category": category,
                    "start": match.start(),
                    "end": match.end(),
                    "matched_text": match.group(0),
                }
            )
    return sorted(findings, key=lambda item: (item["start"], item["end"], item["category"]))


def aggregate_gate(texts: Iterable[str]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    scanned = 0
    affected = 0
    for text in texts:
        scanned += 1
        findings = scan_text(text)
        if findings:
            affected += 1
            counts.update(item["category"] for item in findings)
    return {
        "status": "pass" if not counts else "fail",
        "texts_scanned": scanned,
        "texts_with_findings": affected,
        "finding_counts": dict(sorted(counts.items())),
    }
