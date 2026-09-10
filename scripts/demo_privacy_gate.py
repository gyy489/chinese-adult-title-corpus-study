#!/usr/bin/env python3
"""Exercise exact-span remediation and the locator gate on synthetic text."""

from __future__ import annotations

import json

from src.privacy.direct_locator_gate import aggregate_gate
from src.privacy.remediation import SpanReplacement, remediate_records


def main() -> None:
    original = "SYNTHETIC: 聯絡 contact@example.invalid"
    expected = "contact@example.invalid"
    start = original.index(expected)
    records = [{"record_id": "privacy-demo-01", "final_text": original}]
    decisions = {
        "privacy-demo-01": [
            SpanReplacement(start, start + len(expected), expected, "[CONTACT]", "contact")
        ]
    }
    remediated, counts = remediate_records(records, decisions)
    gate = aggregate_gate(row["final_text"] for row in remediated)
    print(json.dumps({"remediation": counts, "gate": gate}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
