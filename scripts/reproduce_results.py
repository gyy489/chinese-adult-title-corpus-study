#!/usr/bin/env python3
"""Reproduce public summaries and figures from released aggregate CSV tables."""

from __future__ import annotations

import argparse
import csv
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TABLES = ROOT / "results" / "tables"


def read_csv(name: str) -> list[dict[str, str]]:
    with (TABLES / name).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def one(rows: list[dict[str, str]], field: str, value: str) -> dict[str, str]:
    matches = [row for row in rows if row.get(field) == value]
    if len(matches) != 1:
        raise ValueError(f"expected one row where {field}={value!r}; got {len(matches)}")
    return matches[0]


def as_int(row: dict[str, str], field: str) -> int:
    return int(row[field].replace(",", ""))


def as_float(row: dict[str, str], field: str) -> float:
    return float(row[field])


def close(actual: float, expected: float, tolerance: float = 0.011) -> None:
    if abs(actual - expected) > tolerance:
        raise ValueError(f"expected {expected}, got {actual}")


def calculate() -> dict[str, object]:
    pipeline_rows = read_csv("corpus_pipeline_counts.csv")
    pipeline = {row["stage"]: row for row in pipeline_rows}
    table2 = read_csv("table_2_target_actor_visibility.csv")
    table3 = read_csv("table_3_cross_stage_consistency.csv")
    table4 = read_csv("table_4_supporting_and_sensitivity.csv")

    collected = as_int(pipeline["collected"], "record_rows")
    prepared = as_int(pipeline["prepared"], "record_rows")
    candidate_records = as_int(pipeline["candidate"], "record_rows")
    candidate_texts = as_int(pipeline["candidate"], "unique_texts")
    valid_outputs = as_int(pipeline["valid_ai_outputs"], "record_rows")
    analytical_texts = as_int(pipeline["analytical_sample"], "unique_texts")
    removed_contributions = as_int(
        pipeline["analytical_sample"], "excluded_or_removed"
    )
    probability = as_int(pipeline["probability_track"], "unique_texts")
    mechanism = as_int(pipeline["mechanism_enriched_track"], "unique_texts")

    if collected - prepared != as_int(pipeline["prepared"], "excluded_or_removed"):
        raise ValueError("prepared-corpus exclusion count is inconsistent")
    if prepared - candidate_records != as_int(
        pipeline["candidate"], "excluded_or_removed"
    ):
        raise ValueError("semantic-length exclusion count is inconsistent")
    if valid_outputs - removed_contributions != analytical_texts:
        raise ValueError("final-text deduplication count is inconsistent")
    if probability + mechanism != analytical_texts:
        raise ValueError("sampling-track counts do not sum to the analytical sample")
    if candidate_texts - analytical_texts != 50_863:
        raise ValueError("candidate-frame remainder is inconsistent")

    target = one(table2, "measure", "Distribution target position visible")
    actor = one(table2, "measure", "Distribution actor position visible")
    target_only = one(
        table2,
        "measure",
        "Target visible; actor outside contract-defined visible set",
    )
    actor_only = one(
        table2,
        "measure",
        "Actor visible; target outside contract-defined visible set",
    )
    both = one(table2, "measure", "Both positions visible")
    neither = one(
        table2, "measure", "Neither position in contract-defined visible set"
    )
    difference = one(table2, "measure", "Target minus actor visibility")
    eligible = as_int(target, "denominator_n")

    if len({as_int(row, "denominator_n") for row in table2}) != 1:
        raise ValueError("primary-result denominators are inconsistent")
    if sum(as_int(row, "selected_n") for row in (target_only, actor_only, both, neither)) != eligible:
        raise ValueError("paired cells do not sum to the eligible denominator")
    close(100 * as_int(target, "selected_n") / eligible, as_float(target, "percent"))
    close(100 * as_int(actor, "selected_n") / eligible, as_float(actor, "percent"))
    close(
        as_float(target, "percent") - as_float(actor, "percent"),
        as_float(difference, "percent"),
    )

    target_composition = [
        row for row in table4 if row["analysis"] == "RQ3"
        and row["manuscript_role"] == "Complete mutually exclusive target composition"
    ]
    if sum(as_int(row, "selected_n") for row in target_composition) != eligible:
        raise ValueError("mutually exclusive target categories do not sum to eligibility")

    cross_stage = []
    for row in table3:
        selected = as_int(row, "exactly_consistent_n")
        denominator = as_int(row, "eligible_n")
        percent = as_float(row, "percent")
        close(100 * selected / denominator, percent)
        cross_stage.append(
            {
                "comparison": row["visible_stage_comparison"],
                "consistent_n": selected,
                "eligible_n": denominator,
                "percent": percent,
            }
        )

    feminine = one(table4, "measure_or_boundary", "Distribution target: feminine")

    return {
        "scope": {
            "source_records": collected,
            "candidate_unique_texts": candidate_texts,
            "valid_record_level_ai_outputs": valid_outputs,
            "analytical_unique_texts": analytical_texts,
            "probability_track_texts": probability,
            "mechanism_enriched_texts": mechanism,
        },
        "primary_result": {
            "eligible_n": eligible,
            "target_visible_n": as_int(target, "selected_n"),
            "target_visible_percent": as_float(target, "percent"),
            "actor_visible_n": as_int(actor, "selected_n"),
            "actor_visible_percent": as_float(actor, "percent"),
            "paired_difference_percentage_points": as_float(difference, "percent"),
            "paired_cells": {
                "target_only_n": as_int(target_only, "selected_n"),
                "actor_only_n": as_int(actor_only, "selected_n"),
                "both_n": as_int(both, "selected_n"),
                "neither_n": as_int(neither, "selected_n"),
            },
        },
        "supporting_result": {
            "strictly_feminine_target_n": as_int(feminine, "selected_n"),
            "eligible_n": as_int(feminine, "eligible_n"),
            "percent": as_float(feminine, "percent"),
        },
        "cross_stage_consistency": cross_stage,
        "inference_boundary": (
            "Describes the retained analytical sample; not a population estimate "
            "for all Chinese-language adult-video titles."
        ),
    }


def corpus_svg(summary: dict[str, object]) -> str:
    scope = summary["scope"]
    assert isinstance(scope, dict)
    stages = [
        ("Source records", f"{scope['source_records']:,} records"),
        ("Candidate frame", f"{scope['candidate_unique_texts']:,} unique texts"),
        ("Valid AI outputs", f"{scope['valid_record_level_ai_outputs']:,} record-level outputs"),
        ("Analytical sample", f"{scope['analytical_unique_texts']:,} unique texts"),
    ]
    blocks = []
    y = 50
    for index, (label, value) in enumerate(stages):
        blocks.append(
            f'<rect x="150" y="{y}" width="700" height="100" rx="10" '
            f'fill="#ffffff" stroke="#1f2937" stroke-width="2"/>'
            f'<text x="500" y="{y + 38}" text-anchor="middle" '
            f'font-family="Arial, sans-serif" font-size="24" font-weight="700">{label}</text>'
            f'<text x="500" y="{y + 74}" text-anchor="middle" '
            f'font-family="Arial, sans-serif" font-size="22">{value}</text>'
        )
        if index < len(stages) - 1:
            blocks.append(
                f'<line x1="500" y1="{y + 100}" x2="500" y2="{y + 145}" '
                'stroke="#1f2937" stroke-width="2" marker-end="url(#arrow)"/>'
            )
        y += 145
    blocks.append(
        f'<text x="500" y="{y + 5}" text-anchor="middle" font-family="Arial, sans-serif" '
        f'font-size="18" fill="#4b5563">{scope["probability_track_texts"]:,} probability-track + '
        f'{scope["mechanism_enriched_texts"]:,} mechanism-enriched texts</text>'
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="680" '
        'viewBox="0 0 1000 680" role="img" aria-labelledby="title desc">'
        '<title id="title">Corpus formation</title>'
        '<desc id="desc">Aggregate flow from source records to the final analytical sample.</desc>'
        '<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="5" refY="3" '
        'orient="auto" markerUnits="strokeWidth"><path d="M0,0 L0,6 L6,3 z" fill="#1f2937"/>'
        '</marker></defs><rect width="100%" height="100%" fill="#f8fafc"/>'
        + "".join(blocks)
        + "</svg>\n"
    )


def visibility_svg(summary: dict[str, object]) -> str:
    primary = summary["primary_result"]
    assert isinstance(primary, dict)
    target = float(primary["target_visible_percent"])
    actor = float(primary["actor_visible_percent"])

    def bar(y: int, label: str, value: float, color: str) -> str:
        width = value * 6
        return (
            f'<text x="80" y="{y + 30}" font-family="Arial, sans-serif" font-size="24">{label}</text>'
            f'<rect x="340" y="{y}" width="600" height="48" fill="#e5e7eb" rx="5"/>'
            f'<rect x="340" y="{y}" width="{width:.2f}" height="48" fill="{color}" rx="5"/>'
            f'<text x="975" y="{y + 32}" font-family="Arial, sans-serif" '
            f'font-size="22" font-weight="700">{value:.2f}%</text>'
        )

    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="400" '
        'viewBox="0 0 1200 400" role="img" aria-labelledby="title desc">'
        '<title id="title">Distribution target and actor visibility</title>'
        f'<desc id="desc">Among {primary["eligible_n"]:,} eligible titles, target visibility was '
        f'{target:.2f} percent and actor visibility was {actor:.2f} percent.</desc>'
        '<rect width="100%" height="100%" fill="#f8fafc"/>'
        '<text x="600" y="55" text-anchor="middle" font-family="Arial, sans-serif" '
        f'font-size="28" font-weight="700">Target-actor visibility (n = {primary["eligible_n"]:,})</text>'
        + bar(110, "Target position", target, "#2563eb")
        + bar(205, "Actor position", actor, "#0f766e")
        + f'<text x="600" y="335" text-anchor="middle" font-family="Arial, sans-serif" '
        f'font-size="21">Paired difference: {primary["paired_difference_percentage_points"]:.2f} percentage points</text></svg>\n'
    )


def summary_markdown(summary: dict[str, object]) -> str:
    scope = summary["scope"]
    primary = summary["primary_result"]
    supporting = summary["supporting_result"]
    assert isinstance(scope, dict) and isinstance(primary, dict) and isinstance(supporting, dict)
    return f"""# Reproduced public result summary

- Analytical sample: **{scope['analytical_unique_texts']:,} unique final de-identified title texts**.
- Sampling composition: **{scope['probability_track_texts']:,} probability-track** and **{scope['mechanism_enriched_texts']:,} mechanism-enriched** texts.
- Primary opportunity denominator: **{primary['eligible_n']:,} titles**.
- Target position visible: **{primary['target_visible_percent']:.2f}%** ({primary['target_visible_n']:,}/{primary['eligible_n']:,}).
- Actor position visible: **{primary['actor_visible_percent']:.2f}%** ({primary['actor_visible_n']:,}/{primary['eligible_n']:,}).
- Paired target-minus-actor difference: **{primary['paired_difference_percentage_points']:.2f} percentage points**.
- Strictly feminine-coded distribution target: **{supporting['percent']:.2f}%** ({supporting['strictly_feminine_target_n']:,}/{supporting['eligible_n']:,}).

These values describe the retained analytical sample and are not population estimates for all Chinese-language adult-video titles.
"""


def render(output_root: Path) -> None:
    summary = calculate()
    reproduced = output_root / "reproduced"
    figures = output_root / "figures"
    reproduced.mkdir(parents=True, exist_ok=True)
    figures.mkdir(parents=True, exist_ok=True)
    (reproduced / "headline_metrics.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (reproduced / "summary.md").write_text(summary_markdown(summary), encoding="utf-8")
    (figures / "corpus_flow.svg").write_text(corpus_svg(summary), encoding="utf-8")
    (figures / "target_actor_visibility.svg").write_text(
        visibility_svg(summary), encoding="utf-8"
    )


def check() -> None:
    relative_paths = (
        Path("reproduced/headline_metrics.json"),
        Path("reproduced/summary.md"),
        Path("figures/corpus_flow.svg"),
        Path("figures/target_actor_visibility.svg"),
    )
    with tempfile.TemporaryDirectory() as temporary:
        candidate = Path(temporary)
        render(candidate)
        mismatches = [
            str(path)
            for path in relative_paths
            if not (ROOT / "results" / path).exists()
            or (candidate / path).read_bytes() != (ROOT / "results" / path).read_bytes()
        ]
    if mismatches:
        raise SystemExit("generated outputs differ: " + ", ".join(mismatches))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        check()
    else:
        render(ROOT / "results")


if __name__ == "__main__":
    main()
