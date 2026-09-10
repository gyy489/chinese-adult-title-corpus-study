#!/usr/bin/env python3
"""Aggregate a synthetic blinded-review fixture."""

from __future__ import annotations

import json
from pathlib import Path

from src.human_review.agreement import compare


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    rows = json.loads(
        (ROOT / "examples" / "synthetic" / "human_review_labels.json").read_text(
            encoding="utf-8"
        )
    )
    output = {
        "coder_a_vs_coder_b": compare(rows["coder_a"], rows["coder_b"]),
        "coder_a_vs_ai": compare(rows["coder_a"], rows["ai"]),
        "coder_b_vs_ai": compare(rows["coder_b"], rows["ai"]),
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
