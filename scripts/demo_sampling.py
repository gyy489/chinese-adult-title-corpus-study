#!/usr/bin/env python3
"""Run both sampling tracks on a small synthetic frame."""

from __future__ import annotations

import json
from pathlib import Path

from src.sampling.fixed_seed import (
    design_summary,
    mechanism_enriched_sample,
    stratified_sample,
)


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    frame = json.loads(
        (ROOT / "examples" / "synthetic" / "sampling_frame.json").read_text(
            encoding="utf-8"
        )
    )
    strata = ("source_alias", "length_band")
    probability = stratified_sample(frame, sample_size=6, strata=strata, seed=2026081602)
    enriched = mechanism_enriched_sample(
        frame,
        group_field="mechanism_group",
        group_quotas={"distribution": 1, "exposure": 1},
        seed=2026081603,
        excluded_ids=(row["record_id"] for row in probability),
    )
    print(json.dumps(design_summary([*probability, *enriched], strata=strata), indent=2))


if __name__ == "__main__":
    main()
