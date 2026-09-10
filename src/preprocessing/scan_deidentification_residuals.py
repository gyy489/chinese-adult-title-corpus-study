"""Public, dictionary-free residual-identifier scanner.

The production scanner also consumes restricted recurring-alias and
organization dictionaries. This release keeps the structural checks and the
same row-oriented output contract, while deliberately omitting those literals.
"""

from __future__ import annotations

import re

SCAN_VERSION = "v1.1-public-structural-interface"
REPLACEMENT_TERMS = {
    "某人", "某机构", "某同学", "某哥哥", "某妹妹", "某姐姐", "某学妹",
    "某学姐", "某学弟", "某学长", "某小姐", "某师姐", "某老师", "某酱",
    "某君", "某账号",
}

PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("email_address", "high", re.compile(
        r"(?i)(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?!\w)"
    )),
    ("url_or_domain", "high", re.compile(
        r"(?i)(?:https?://|www\.)\S+|(?<![\w@])(?:[a-z0-9-]+\.)+"
        r"(?:com|cn|net|org|tv|me|cc|xyz)(?:/\S*)?"
    )),
    ("cn_mobile_number", "high", re.compile(
        r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)"
    )),
    ("explicit_at_handle", "high", re.compile(
        r"(?<![\w@])@[A-Za-z0-9_][A-Za-z0-9_.-]{2,31}"
    )),
    ("long_digit_sequence", "medium", re.compile(r"(?<!\d)\d{7,19}(?!\d)")),
)


def _context(text: str, start: int, end: int, width: int = 48) -> str:
    return text[max(0, start - width) : min(len(text), end + width)]


def scan_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for row in rows:
        title = row.get("deidentified_title", "") or row.get("cleaned_title", "")
        for scan_type, priority, pattern in PATTERNS:
            for match in pattern.finditer(title):
                matched = match.group(0)
                if matched in REPLACEMENT_TERMS or "某" in matched:
                    continue
                items.append(
                    {
                        "scan_type": scan_type,
                        "priority": priority,
                        "record_id": row.get("record_id", ""),
                        "source_site": row.get("source_site", ""),
                        "data_round": row.get("data_round", ""),
                        "matched_text": matched,
                        "span_start": str(match.start()),
                        "span_end": str(match.end()),
                        "context": _context(title, match.start(), match.end()),
                        "deidentified_title": title,
                        "review_decision": "",
                        "review_note": (
                            "Public structural layer; literal identity dictionaries are restricted."
                        ),
                    }
                )
    return items
