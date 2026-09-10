"""Audit label-independent representative choices for repeated final texts.

The audit reads immutable v1 membership and coding outputs, compares three
deterministic representative rules, and emits aggregate counts only. It does
not expose title text, source sites, record identifiers, or model evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .analyze_gendered_visibility_paper_corpus_v1 import (
        estimate_metric,
        load_units,
        metric_specs,
        read_csv,
    )
except ImportError:  # Direct script execution.
    from analyze_gendered_visibility_paper_corpus_v1 import (
        estimate_metric,
        load_units,
        metric_specs,
        read_csv,
    )

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = Path(__file__).resolve()
V1_MANIFEST = ROOT / "data/processed/gendered_visibility_paper_corpus_v1/manifest.json"
V1_MEMBERSHIP = (
    ROOT
    / "data/processed/gendered_visibility_paper_corpus_v1/private/"
    "analytical_corpus_membership.csv"
)
V2_MEMBERSHIP = (
    ROOT
    / "data/processed/gendered_visibility_paper_corpus_v2_text_deduplicated/"
    "private/analytical_corpus_membership.csv"
)
DEFAULT_OUTPUT_DIR = (
    ROOT
    / "results/tables/gendered_visibility_duplicate_representative_audit_v1"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def relative(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def select_specs() -> dict[str, Any]:
    wanted = {
        "distribution_target_position_visible",
        "distribution_actor_position_visible",
        "target_minus_actor_visibility_paired_difference",
    }
    return {spec.metric: spec for spec in metric_specs() if spec.metric in wanted}


def summarize(units: list[Any]) -> dict[str, Any]:
    specs = select_specs()
    target = estimate_metric(specs["distribution_target_position_visible"], units)
    actor = estimate_metric(specs["distribution_actor_position_visible"], units)
    paired = estimate_metric(
        specs["target_minus_actor_visibility_paired_difference"], units
    )
    return {
        "unique_text_n": len(units),
        "opportunity_n": int(paired["denominator_n"]),
        "target_visible_n": int(target["selected_n"]),
        "target_visible_rate": float(target["estimate"]),
        "actor_visible_n": int(actor["selected_n"]),
        "actor_visible_rate": float(actor["estimate"]),
        "target_only_n": int(paired["selected_n"]),
        "actor_only_n": int(paired["comparison_selected_n"]),
        "paired_difference": float(paired["estimate"]),
    }


def run_audit() -> dict[str, Any]:
    units, _freeze = load_units()
    membership = read_csv(V1_MEMBERSHIP)
    if len(units) != 38_623 or len(membership) != 38_623:
        raise RuntimeError("v1 unit count drifted")

    groups: dict[str, list[tuple[dict[str, str], Any]]] = defaultdict(list)
    for member, unit in zip(membership, units, strict=True):
        if member["title_sha256"] != unit.title_sha256:
            raise RuntimeError("v1 membership/unit ordering drifted")
        groups[member["title_sha256"]].append((member, unit))
    if len(groups) != 38_300:
        raise RuntimeError("distinct final-text count drifted")

    policy_functions = {
        "minimum_record_key_sha256": lambda group: min(
            group,
            key=lambda pair: (
                pair[0]["record_key_sha256"],
                pair[0]["source_layer"],
                int(pair[0]["source_item_index"]),
            ),
        ),
        "maximum_record_key_sha256": lambda group: max(
            group,
            key=lambda pair: (
                pair[0]["record_key_sha256"],
                pair[0]["source_layer"],
                int(pair[0]["source_item_index"]),
            ),
        ),
        "first_v1_corpus_index": lambda group: min(
            group, key=lambda pair: int(pair[0]["corpus_index"])
        ),
    }
    selected_by_policy: dict[str, list[tuple[dict[str, str], Any]]] = {}
    for name, choose in policy_functions.items():
        selected_by_policy[name] = [choose(group) for group in groups.values()]

    current_v2_keys = {
        row["record_key_sha256"] for row in read_csv(V2_MEMBERSHIP)
    }
    minimum_keys = {
        member["record_key_sha256"]
        for member, _unit in selected_by_policy["minimum_record_key_sha256"]
    }
    if current_v2_keys != minimum_keys:
        raise RuntimeError("current v2 membership does not match minimum-key policy")

    repeated = [group for group in groups.values() if len(group) > 1]
    full_output_variation_groups = sum(
        len(
            {
                json.dumps(unit.output, ensure_ascii=False, sort_keys=True)
                for _member, unit in group
            }
        )
        > 1
        for group in repeated
    )

    policy_results = {
        name: summarize([unit for _member, unit in selected])
        for name, selected in selected_by_policy.items()
    }
    differences = [
        result["paired_difference"] for result in policy_results.values()
    ]
    return {
        "created_at": utc_now(),
        "audit_id": "gendered_visibility_duplicate_representative_audit_v1",
        "status": "complete_aggregate_only",
        "scope": (
            "Sensitivity of the primary B1 comparison to three deterministic, "
            "label-independent representatives for each repeated final-text hash."
        ),
        "counts": {
            "v1_valid_record_rows": len(units),
            "distinct_final_text_hashes": len(groups),
            "repeated_final_text_groups": len(repeated),
            "extra_rows_beyond_first": sum(len(group) - 1 for group in repeated),
            "repeated_groups_with_any_full_output_variation": (
                full_output_variation_groups
            ),
        },
        "current_v2_membership_matches_minimum_key_policy": True,
        "policy_results": policy_results,
        "paired_difference_range": {
            "minimum": min(differences),
            "maximum": max(differences),
            "range_width": max(differences) - min(differences),
        },
        "interpretation": (
            "The audit does not justify treating duplicate outputs as identical. "
            "It only measures whether the primary paired difference changes under "
            "three deterministic representative rules."
        ),
        "privacy": "Aggregate-only output; no title or record-level content emitted.",
        "inputs": {
            relative(path): sha256_file(path)
            for path in (V1_MANIFEST, V1_MEMBERSHIP, V2_MEMBERSHIP, SCRIPT)
        },
        "api_calls": 0,
        "raw_data_files_read": 0,
        "frozen_outputs_rewritten": 0,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = run_audit()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / "audit.json"
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
