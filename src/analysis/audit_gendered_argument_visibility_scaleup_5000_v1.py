"""Post-hoc exploratory audit of the frozen 5,000-record v0.3 scale-up.

Public outputs are aggregate-only. They never contain titles, evidence spans,
record identifiers, item indices, literal source names, or provider responses.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from scipy.special import expit
from scipy.stats import chi2, fisher_exact

from src.analysis.analyze_gendered_argument_visibility_deep_mining_v1 import (
    fit_firth,
    fit_firth_lbfgs,
)
from src.analysis.summarize_gendered_argument_visibility_scaleup_5000_v1 import (
    SUBJECTIVITY,
    control_group,
    exact_contrast,
    load_rows,
    paired_counts,
    position_visible,
    sha256,
    weighted_ratio,
    write_csv,
    write_json,
)

ROOT = Path(__file__).resolve().parents[2]
RESULTS = (
    ROOT
    / "results/tables/gendered_argument_visibility_scaleup_5000_audit_v1"
)
PLAN = ROOT / "docs/gendered_argument_visibility_exploratory_audit_plan_v1.md"
CANDIDATE_FREEZE = (
    ROOT / "docs/gendered_argument_visibility_audit_candidate_freeze_v1.md"
)
CODEBOOK = ROOT / "config/gendered_argument_visibility_codebook_v0_3.json"
SAMPLE = (
    ROOT
    / "data/interim/51_gendered_argument_visibility_scaleup_5000_v1/private"
    / "sample_manifest.csv"
)

TRACKS = ("probability", "mechanism", "combined_descriptive")
EDITORIAL_CUES = (
    "erotic_promotional_approval",
    "novelty_or_sensationalization",
    "authenticity_or_voyeurism",
    "mockery_or_humiliation",
    "moral_condemnation",
    "neutral_catalogue",
    "no_visible_editorial_frame",
)
CONTEXT_CUES = (
    "explicit_roleplay_or_bdsm",
    "relationship_entitlement_explicit",
    "institutional_hierarchy_explicit",
    "economic_transaction_explicit",
    "incapacity_or_deception_explicit",
    "force_or_threat_explicit",
    "aftermath_consequence_or_help_seeking",
)
CONTROL_FORMS = (
    "overt_force",
    "restraint_or_confinement",
    "command_or_discipline",
    "threat",
    "deception_or_incapacity",
    "authority_or_economic_leverage",
    "role_label_only",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def source_aliases(rows: Iterable[dict[str, Any]]) -> dict[str, str]:
    return {
        source: f"source_{index:02d}"
        for index, source in enumerate(
            sorted({str(row["source_site"]) for row in rows}), 1
        )
    }


def track_rows(
    rows: list[dict[str, Any]], track: str
) -> list[dict[str, Any]]:
    if track == "combined_descriptive":
        return rows
    return [row for row in rows if row["sampling_track"] == track]


def safe_rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else math.nan


def analytic_valid(row: dict[str, Any]) -> bool:
    return bool(row["valid"]) and (row.get("output") or {}).get(
        "record_validity"
    ) == "valid"


def fisher_binary(
    first_positive: int,
    first_n: int,
    second_positive: int,
    second_n: int,
) -> tuple[float, float]:
    if not first_n or not second_n:
        return math.nan, math.nan
    odds, p_value = fisher_exact(
        [
            [first_positive, first_n - first_positive],
            [second_positive, second_n - second_positive],
        ]
    )
    return float(odds), float(p_value)


def bh_adjust(rows: list[dict[str, Any]]) -> None:
    """Add BH q-values within each explicitly named test family."""
    by_family: dict[str, list[tuple[int, float]]] = {}
    for index, row in enumerate(rows):
        p_value = row.get("p_raw")
        if isinstance(p_value, (float, int)) and math.isfinite(float(p_value)):
            by_family.setdefault(str(row["family"]), []).append(
                (index, float(p_value))
            )
    for tests in by_family.values():
        ordered = sorted(tests, key=lambda item: item[1])
        count = len(ordered)
        adjusted = [1.0] * count
        running = 1.0
        for reverse_index in range(count - 1, -1, -1):
            rank = reverse_index + 1
            running = min(running, ordered[reverse_index][1] * count / rank)
            adjusted[reverse_index] = min(1.0, running)
        for (row_index, _), q_value in zip(ordered, adjusted, strict=True):
            rows[row_index]["q_bh"] = q_value
    for row in rows:
        row.setdefault("q_bh", math.nan)


def quality_by_group(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    aliases = source_aliases(rows)
    groups: list[tuple[str, str, list[dict[str, Any]]]] = [("all", "all", rows)]
    dimensions: list[tuple[str, Callable[[dict[str, Any]], str]]] = [
        ("track", lambda row: str(row["sampling_track"])),
        ("wave", lambda row: str(row["incremental_wave"])),
        ("source", lambda row: aliases[str(row["source_site"])]),
        ("length", lambda row: str(row["length_band"])),
        ("sampling_stratum", lambda row: str(row["sampling_stratum"])),
    ]
    for dimension, getter in dimensions:
        values = sorted({getter(row) for row in rows})
        for value in values:
            groups.append(
                (dimension, value, [row for row in rows if getter(row) == value])
            )
    table = []
    for dimension, level, subset in groups:
        structural_valid = [row for row in subset if row["valid"]]
        valid = [row for row in subset if analytic_valid(row)]
        action_count = sum(len(row.get("normalization_actions") or []) for row in valid)
        table.append(
            {
                "dimension": dimension,
                "level": level,
                "records_n": len(subset),
                "structurally_valid_n": len(structural_valid),
                "analytic_valid_n": len(valid),
                "technical_noise_n": sum(
                    (row.get("output") or {}).get("record_validity")
                    == "technical_noise"
                    for row in structural_valid
                ),
                "unresolved_n": len(subset) - len(structural_valid),
                "structural_valid_rate": safe_rate(
                    len(structural_valid), len(subset)
                ),
                "analytic_valid_rate": safe_rate(len(valid), len(subset)),
                "normalized_n": sum(
                    bool(row.get("normalization_actions")) for row in valid
                ),
                "normalized_rate": safe_rate(
                    sum(bool(row.get("normalization_actions")) for row in valid),
                    len(valid),
                ),
                "normalization_actions_n": action_count,
                "actions_per_valid_record": safe_rate(action_count, len(valid)),
                "evidence_complete_n": sum(
                    bool((row.get("derived") or {}).get("evidence_complete"))
                    for row in valid
                ),
                "evidence_complete_rate": safe_rate(
                    sum(
                        bool((row.get("derived") or {}).get("evidence_complete"))
                        for row in valid
                    ),
                    len(valid),
                ),
                "needs_adjudication_n": sum(
                    bool(row["output"].get("needs_adjudication")) for row in valid
                ),
                "needs_adjudication_rate": safe_rate(
                    sum(
                        bool(row["output"].get("needs_adjudication"))
                        for row in valid
                    ),
                    len(valid),
                ),
            }
        )
    return table


def sentinel_table(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    table = []
    valid = [row for row in rows if analytic_valid(row)]
    for track in TRACKS:
        subset = track_rows(valid, track)
        if not subset:
            continue
        fields = sorted(subset[0]["output"])
        for field in fields:
            values = [row["output"].get(field) for row in subset]
            if any(isinstance(value, (dict, list)) for value in values):
                continue
            counts = Counter(str(value) for value in values)
            table.append(
                {
                    "track": track,
                    "field": field,
                    "denominator_n": len(values),
                    "unclear_n": counts["unclear"],
                    "unclear_rate": safe_rate(counts["unclear"], len(values)),
                    "not_applicable_n": counts["not_applicable"],
                    "not_applicable_rate": safe_rate(
                        counts["not_applicable"], len(values)
                    ),
                    "not_visible_n": counts["not_visible"],
                    "not_visible_rate": safe_rate(counts["not_visible"], len(values)),
                }
            )
    return table


def normalization_action_profile(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prefixes: Counter[str] = Counter()
    records: dict[str, set[int]] = {}
    evidence_prefixes = {
        "inherit_evidence",
        "drop_evidence_support",
        "drop_empty_evidence_row",
    }
    schema_prefixes = {"remove_extra"}
    for row_number, row in enumerate(rows):
        for action in row.get("normalization_actions") or []:
            if isinstance(action, dict):
                prefix = str(action.get("action") or "dict_action")
            else:
                prefix = str(action).split(":", 1)[0]
            prefixes[prefix] += 1
            records.setdefault(prefix, set()).add(row_number)
    table = []
    for prefix, count in sorted(prefixes.items(), key=lambda item: (-item[1], item[0])):
        if prefix in evidence_prefixes:
            family = "evidence_linkage"
        elif prefix in schema_prefixes:
            family = "schema_cleanup"
        else:
            family = "value_or_dependency_normalization"
        table.append(
            {
                "action_family": family,
                "action_prefix": prefix,
                "records_affected_n": len(records[prefix]),
                "actions_n": count,
            }
        )
    return table


def substantive_sensitivity(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    valid = [row for row in rows if analytic_valid(row)]
    variants: tuple[
        tuple[str, Callable[[dict[str, Any]], bool]], ...
    ] = (
        ("all_valid", lambda row: True),
        ("no_normalization", lambda row: not row.get("normalization_actions")),
        (
            "evidence_complete",
            lambda row: bool((row.get("derived") or {}).get("evidence_complete")),
        ),
        (
            "no_normalization_and_evidence_complete",
            lambda row: not row.get("normalization_actions")
            and bool((row.get("derived") or {}).get("evidence_complete")),
        ),
        ("normalized_only", lambda row: bool(row.get("normalization_actions"))),
        (
            "evidence_incomplete_only",
            lambda row: not bool(
                (row.get("derived") or {}).get("evidence_complete")
            ),
        ),
    )
    table = []
    for track in ("probability", "mechanism"):
        base = [row for row in valid if row["sampling_track"] == track]
        for variant, predicate in variants:
            subset = [row for row in base if predicate(row)]
            distribution = [
                row
                for row in subset
                if row["output"]["distribution_visibility"] == "visible"
            ]
            pair = paired_counts(
                distribution,
                lambda output: position_visible(
                    output["distribution_target_position"]
                ),
                lambda output: position_visible(
                    output["distribution_actor_position"]
                ),
            )
            creation_distribution = [
                row
                for row in subset
                if row["output"]["material_creation_visibility"] == "visible"
                and row["output"]["distribution_visibility"] == "visible"
            ]
            stage_actor_pair = paired_counts(
                creation_distribution,
                lambda output: position_visible(
                    output["material_creation_actor_position"]
                ),
                lambda output: position_visible(
                    output["distribution_actor_position"]
                ),
            )
            control = exact_contrast(
                subset,
                lambda output: output["objectification_target_position"]
                == "feminine",
            )
            table.append(
                {
                    "track": track,
                    "variant": variant,
                    "analysis_n": len(subset),
                    "privacy_opportunity_n": pair["denominator_n"],
                    "privacy_first_only_n": pair["first_only_n"],
                    "privacy_second_only_n": pair["second_only_n"],
                    "privacy_target_minus_actor_rate": pair[
                        "first_minus_second_rate"
                    ],
                    "privacy_exact_p": pair["exact_p"],
                    "stage_actor_opportunity_n": stage_actor_pair["denominator_n"],
                    "stage_actor_first_only_n": stage_actor_pair["first_only_n"],
                    "stage_actor_second_only_n": stage_actor_pair["second_only_n"],
                    "creation_minus_distribution_actor_rate": stage_actor_pair[
                        "first_minus_second_rate"
                    ],
                    "stage_actor_exact_p": stage_actor_pair["exact_p"],
                    "control_direct_positive_n": control["direct_positive_n"],
                    "control_direct_n": control["direct_denominator_n"],
                    "control_force_positive_n": control["force_positive_n"],
                    "control_force_n": control["force_denominator_n"],
                    "control_direct_minus_force_rate": control[
                        "direct_minus_force_risk_difference"
                    ],
                    "control_fisher_p": control["fisher_two_sided_p"],
                }
            )
    return table


def distribution_gender_table(
    rows: list[dict[str, Any]], tests: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    valid = [row for row in rows if analytic_valid(row)]
    table = []
    categories: tuple[tuple[str, Callable[[str], bool]], ...] = (
        ("feminine", lambda value: value == "feminine"),
        ("mixed_multiple", lambda value: value == "mixed_multiple"),
        ("feminine_or_mixed", lambda value: value in {"feminine", "mixed_multiple"}),
        ("masculine", lambda value: value == "masculine"),
        ("gender_diverse", lambda value: value == "gender_diverse"),
        ("gender_unspecified", lambda value: value == "gender_unspecified"),
        ("mutual_or_reciprocal", lambda value: value == "mutual_or_reciprocal"),
        ("not_visible", lambda value: value == "not_visible"),
        ("unclear", lambda value: value == "unclear"),
        ("not_applicable", lambda value: value == "not_applicable"),
    )
    for track in TRACKS:
        distribution = [
            row
            for row in track_rows(valid, track)
            if row["output"]["distribution_visibility"] == "visible"
        ]
        for category, predicate in categories:
            selected = [
                row
                for row in distribution
                if predicate(row["output"]["distribution_target_position"])
            ]
            pair = paired_counts(
                selected,
                lambda output: position_visible(
                    output["distribution_target_position"]
                ),
                lambda output: position_visible(
                    output["distribution_actor_position"]
                ),
            )
            actor_visible = sum(
                position_visible(row["output"]["distribution_actor_position"])
                for row in selected
            )
            table.append(
                {
                    "track": track,
                    "target_category": category,
                    "selected_n": len(selected),
                    "distribution_denominator_n": len(distribution),
                    "selected_rate": safe_rate(len(selected), len(distribution)),
                    "actor_visible_n": actor_visible,
                    "actor_visible_rate": safe_rate(actor_visible, len(selected)),
                    **{
                        key: value
                        for key, value in pair.items()
                        if key != "denominator_n"
                    },
                }
            )
        feminine = [
            row
            for row in distribution
            if row["output"]["distribution_target_position"] == "feminine"
        ]
        other_visible = [
            row
            for row in distribution
            if position_visible(row["output"]["distribution_target_position"])
            and row["output"]["distribution_target_position"] != "feminine"
        ]
        feminine_actor = sum(
            position_visible(row["output"]["distribution_actor_position"])
            for row in feminine
        )
        other_actor = sum(
            position_visible(row["output"]["distribution_actor_position"])
            for row in other_visible
        )
        odds, p_value = fisher_binary(
            feminine_actor, len(feminine), other_actor, len(other_visible)
        )
        tests.append(
            {
                "family": "E1_target_gender",
                "track": track,
                "comparison": "feminine_vs_other_visible_target__actor_visible",
                "first_positive_n": feminine_actor,
                "first_n": len(feminine),
                "second_positive_n": other_actor,
                "second_n": len(other_visible),
                "difference": safe_rate(feminine_actor, len(feminine))
                - safe_rate(other_actor, len(other_visible)),
                "odds_ratio": odds,
                "p_raw": p_value,
            }
        )
    return table


def authorization_tables(
    rows: list[dict[str, Any]], tests: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    valid = [row for row in rows if analytic_valid(row)]
    summary = []
    realizations = []
    authorization_levels = (
        "authorized_explicit",
        "unauthorized_explicit",
        "mixed_or_stage_conflict",
        "no_visible_authorization_language",
        "unclear",
        "not_applicable",
    )
    for track in TRACKS:
        distribution = [
            row
            for row in track_rows(valid, track)
            if row["output"]["distribution_visibility"] == "visible"
        ]
        by_level = {
            level: [
                row
                for row in distribution
                if row["output"]["distribution_authorization_visibility"] == level
            ]
            for level in authorization_levels
        }
        for level, subset in by_level.items():
            actor_visible = sum(
                position_visible(row["output"]["distribution_actor_position"])
                for row in subset
            )
            target_visible = sum(
                position_visible(row["output"]["distribution_target_position"])
                for row in subset
            )
            summary.append(
                {
                    "track": track,
                    "authorization_visibility": level,
                    "selected_n": len(subset),
                    "distribution_denominator_n": len(distribution),
                    "selected_rate": safe_rate(len(subset), len(distribution)),
                    "actor_visible_n": actor_visible,
                    "actor_visible_rate": safe_rate(actor_visible, len(subset)),
                    "target_visible_n": target_visible,
                    "target_visible_rate": safe_rate(target_visible, len(subset)),
                }
            )
            realization_counts = Counter(
                row["output"]["distribution_actor_realization"] for row in subset
            )
            for realization, count in sorted(realization_counts.items()):
                realizations.append(
                    {
                        "track": track,
                        "authorization_visibility": level,
                        "actor_realization": realization,
                        "selected_n": count,
                        "authorization_denominator_n": len(subset),
                        "selected_rate": safe_rate(count, len(subset)),
                    }
                )
        baseline = by_level["no_visible_authorization_language"]
        baseline_actor = sum(
            position_visible(row["output"]["distribution_actor_position"])
            for row in baseline
        )
        for level in (
            "authorized_explicit",
            "unauthorized_explicit",
            "mixed_or_stage_conflict",
        ):
            subset = by_level[level]
            actor_visible = sum(
                position_visible(row["output"]["distribution_actor_position"])
                for row in subset
            )
            odds, p_value = fisher_binary(
                actor_visible, len(subset), baseline_actor, len(baseline)
            )
            tests.append(
                {
                    "family": "E2_authorization",
                    "track": track,
                    "comparison": f"{level}_vs_no_visible_language__actor_visible",
                    "first_positive_n": actor_visible,
                    "first_n": len(subset),
                    "second_positive_n": baseline_actor,
                    "second_n": len(baseline),
                    "difference": safe_rate(actor_visible, len(subset))
                    - safe_rate(baseline_actor, len(baseline)),
                    "odds_ratio": odds,
                    "p_raw": p_value,
                }
            )
    return summary, realizations


def editorial_distribution_table(
    rows: list[dict[str, Any]], tests: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    valid = [row for row in rows if analytic_valid(row)]
    table = []
    for track in TRACKS:
        distribution = [
            row
            for row in track_rows(valid, track)
            if row["output"]["distribution_visibility"] == "visible"
        ]
        for cue in EDITORIAL_CUES:
            present = [
                row
                for row in distribution
                if cue in row["output"]["editorial_framing_cues"]
            ]
            absent = [row for row in distribution if row not in present]

            def metrics(subset: list[dict[str, Any]]) -> tuple[int, int, int]:
                actor = sum(
                    position_visible(row["output"]["distribution_actor_position"])
                    for row in subset
                )
                target = sum(
                    position_visible(row["output"]["distribution_target_position"])
                    for row in subset
                )
                feminine = sum(
                    row["output"]["distribution_target_position"] == "feminine"
                    for row in subset
                )
                return actor, target, feminine

            present_actor, present_target, present_feminine = metrics(present)
            absent_actor, _absent_target, absent_feminine = metrics(absent)
            pair = paired_counts(
                present,
                lambda output: position_visible(
                    output["distribution_target_position"]
                ),
                lambda output: position_visible(
                    output["distribution_actor_position"]
                ),
            )
            actor_odds, actor_p = fisher_binary(
                present_actor, len(present), absent_actor, len(absent)
            )
            gender_odds, gender_p = fisher_binary(
                present_feminine, len(present), absent_feminine, len(absent)
            )
            table.append(
                {
                    "track": track,
                    "editorial_cue": cue,
                    "present_n": len(present),
                    "absent_n": len(absent),
                    "present_actor_visible_n": present_actor,
                    "present_actor_visible_rate": safe_rate(
                        present_actor, len(present)
                    ),
                    "absent_actor_visible_n": absent_actor,
                    "absent_actor_visible_rate": safe_rate(absent_actor, len(absent)),
                    "actor_visible_rate_difference": safe_rate(
                        present_actor, len(present)
                    )
                    - safe_rate(absent_actor, len(absent)),
                    "present_target_visible_rate": safe_rate(
                        present_target, len(present)
                    ),
                    "present_feminine_target_rate": safe_rate(
                        present_feminine, len(present)
                    ),
                    "absent_feminine_target_rate": safe_rate(
                        absent_feminine, len(absent)
                    ),
                    "feminine_target_rate_difference": safe_rate(
                        present_feminine, len(present)
                    )
                    - safe_rate(absent_feminine, len(absent)),
                    "present_target_minus_actor_rate": pair[
                        "first_minus_second_rate"
                    ],
                    "present_first_only_n": pair["first_only_n"],
                    "present_second_only_n": pair["second_only_n"],
                }
            )
            tests.extend(
                [
                    {
                        "family": "E3_editorial_actor",
                        "track": track,
                        "comparison": f"{cue}__actor_visible",
                        "first_positive_n": present_actor,
                        "first_n": len(present),
                        "second_positive_n": absent_actor,
                        "second_n": len(absent),
                        "difference": safe_rate(present_actor, len(present))
                        - safe_rate(absent_actor, len(absent)),
                        "odds_ratio": actor_odds,
                        "p_raw": actor_p,
                    },
                    {
                        "family": "E3_editorial_gender",
                        "track": track,
                        "comparison": f"{cue}__feminine_target",
                        "first_positive_n": present_feminine,
                        "first_n": len(present),
                        "second_positive_n": absent_feminine,
                        "second_n": len(absent),
                        "difference": safe_rate(present_feminine, len(present))
                        - safe_rate(absent_feminine, len(absent)),
                        "odds_ratio": gender_odds,
                        "p_raw": gender_p,
                    },
                ]
            )
    return table


def editorial_candidate_robustness(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    valid = [row for row in rows if analytic_valid(row)]
    aliases = source_aliases(valid)
    table = []
    for track in ("probability", "mechanism"):
        base = [row for row in valid if row["sampling_track"] == track]
        variants: list[tuple[str, str, list[dict[str, Any]]]] = [
            ("main", "all", base)
        ]
        variants.extend(
            (
                "leave_one_source_out",
                alias,
                [row for row in base if row["source_site"] != source],
            )
            for source, alias in aliases.items()
        )
        variants.extend(
            (
                "leave_one_length_out",
                str(length),
                [row for row in base if row["length_band"] != length],
            )
            for length in sorted({row["length_band"] for row in base})
        )
        for variant, removed, subset in variants:
            distribution = [
                row
                for row in subset
                if row["output"]["distribution_visibility"] == "visible"
            ]
            for cue in ("authenticity_or_voyeurism", "moral_condemnation"):
                present = [
                    row
                    for row in distribution
                    if cue in row["output"]["editorial_framing_cues"]
                ]
                absent = [row for row in distribution if row not in present]
                present_actor = sum(
                    position_visible(row["output"]["distribution_actor_position"])
                    for row in present
                )
                absent_actor = sum(
                    position_visible(row["output"]["distribution_actor_position"])
                    for row in absent
                )
                table.append(
                    {
                        "track": track,
                        "variant": variant,
                        "removed_level": removed,
                        "editorial_cue": cue,
                        "present_actor_visible_n": present_actor,
                        "present_n": len(present),
                        "absent_actor_visible_n": absent_actor,
                        "absent_n": len(absent),
                        "actor_visible_rate_difference": safe_rate(
                            present_actor, len(present)
                        )
                        - safe_rate(absent_actor, len(absent)),
                    }
                )
    return table


def stage_transition_table(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    valid = [row for row in rows if analytic_valid(row)]
    table = []
    for track in TRACKS:
        subset = track_rows(valid, track)
        creation_distribution = [
            row
            for row in subset
            if row["output"]["material_creation_visibility"] == "visible"
            and row["output"]["distribution_visibility"] == "visible"
        ]
        distribution_exposure = [
            row
            for row in subset
            if row["output"]["distribution_visibility"] == "visible"
            and row["output"]["exposure_visibility"] == "visible"
        ]
        three_stage = [
            row
            for row in subset
            if row["output"]["material_creation_visibility"] == "visible"
            and row["output"]["distribution_visibility"] == "visible"
            and row["output"]["exposure_visibility"] == "visible"
        ]
        specs = (
            (
                "creation_actor_vs_distribution_actor",
                creation_distribution,
                lambda output: position_visible(
                    output["material_creation_actor_position"]
                ),
                lambda output: position_visible(
                    output["distribution_actor_position"]
                ),
            ),
            (
                "creation_target_vs_distribution_target",
                creation_distribution,
                lambda output: position_visible(
                    output["material_creation_target_position"]
                ),
                lambda output: position_visible(
                    output["distribution_target_position"]
                ),
            ),
            (
                "distribution_target_vs_exposure_target",
                distribution_exposure,
                lambda output: position_visible(
                    output["distribution_target_position"]
                ),
                lambda output: position_visible(output["exposure_target_position"]),
            ),
        )
        for comparison, opportunity, first, second in specs:
            if comparison == "creation_target_vs_distribution_target":
                first_field = "material_creation_target_position"
                second_field = "distribution_target_position"
            elif comparison == "distribution_target_vs_exposure_target":
                first_field = "distribution_target_position"
                second_field = "exposure_target_position"
            else:
                first_field = second_field = ""
            both_positions_visible = [
                row
                for row in opportunity
                if first_field
                and position_visible(row["output"][first_field])
                and position_visible(row["output"][second_field])
            ]
            same_visible = sum(
                row["output"][first_field] == row["output"][second_field]
                for row in both_positions_visible
            )
            table.append(
                {
                    "track": track,
                    "comparison": comparison,
                    **paired_counts(opportunity, first, second),
                    "all_positions_visible_n": len(both_positions_visible),
                    "same_visible_position_n": same_visible,
                    "same_visible_position_rate": safe_rate(
                        same_visible, len(both_positions_visible)
                    ),
                }
            )
        visible_three_stage = [
            row
            for row in three_stage
            if all(
                position_visible(row["output"][field])
                for field in (
                    "material_creation_target_position",
                    "distribution_target_position",
                    "exposure_target_position",
                )
            )
        ]
        same_three_stage = sum(
            len(
                {
                    row["output"]["material_creation_target_position"],
                    row["output"]["distribution_target_position"],
                    row["output"]["exposure_target_position"],
                }
            )
            == 1
            for row in visible_three_stage
        )
        table.append(
            {
                "track": track,
                "comparison": "three_stage_object_position_persistence",
                "denominator_n": len(three_stage),
                "both_visible_n": sum(
                    position_visible(
                        row["output"]["material_creation_target_position"]
                    )
                    and position_visible(
                        row["output"]["distribution_target_position"]
                    )
                    and position_visible(row["output"]["exposure_target_position"])
                    for row in three_stage
                ),
                "first_only_n": 0,
                "second_only_n": 0,
                "neither_visible_n": 0,
                "first_minus_second_rate": math.nan,
                "exact_p": math.nan,
                "all_positions_visible_n": len(visible_three_stage),
                "same_visible_position_n": same_three_stage,
                "same_visible_position_rate": safe_rate(
                    same_three_stage, len(visible_three_stage)
                ),
            }
        )
    return table


def stage_persistence_robustness(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    valid = [row for row in rows if analytic_valid(row)]
    aliases = source_aliases(valid)
    table = []

    def persistence(
        subset: list[dict[str, Any]], fields: tuple[str, ...], stages: tuple[str, ...]
    ) -> tuple[int, int, float]:
        opportunity = [
            row
            for row in subset
            if all(row["output"][stage] == "visible" for stage in stages)
            and all(position_visible(row["output"][field]) for field in fields)
        ]
        same = sum(
            len({row["output"][field] for field in fields}) == 1
            for row in opportunity
        )
        return same, len(opportunity), safe_rate(same, len(opportunity))

    comparisons = (
        (
            "creation_distribution_target",
            ("material_creation_target_position", "distribution_target_position"),
            ("material_creation_visibility", "distribution_visibility"),
        ),
        (
            "distribution_exposure_target",
            ("distribution_target_position", "exposure_target_position"),
            ("distribution_visibility", "exposure_visibility"),
        ),
        (
            "creation_distribution_exposure_target",
            (
                "material_creation_target_position",
                "distribution_target_position",
                "exposure_target_position",
            ),
            (
                "material_creation_visibility",
                "distribution_visibility",
                "exposure_visibility",
            ),
        ),
    )
    for track in ("probability", "mechanism"):
        base = [row for row in valid if row["sampling_track"] == track]
        variants: list[tuple[str, str, list[dict[str, Any]]]] = [
            ("main", "all", base)
        ]
        variants.extend(
            (
                "leave_one_source_out",
                alias,
                [row for row in base if row["source_site"] != source],
            )
            for source, alias in aliases.items()
        )
        variants.extend(
            (
                "leave_one_length_out",
                str(length),
                [row for row in base if row["length_band"] != length],
            )
            for length in sorted({row["length_band"] for row in base})
        )
        for variant, removed, subset in variants:
            for comparison, fields, stages in comparisons:
                same, denominator, rate = persistence(subset, fields, stages)
                table.append(
                    {
                        "track": track,
                        "variant": variant,
                        "removed_level": removed,
                        "comparison": comparison,
                        "same_position_n": same,
                        "denominator_n": denominator,
                        "same_position_rate": rate,
                    }
                )
    return table


def feminine_distribution_configuration(
    rows: list[dict[str, Any]], tests: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    valid = [row for row in rows if analytic_valid(row)]
    table = []

    def metrics(output: dict[str, Any]) -> dict[str, bool]:
        subjectivity = {
            "female_desire_visible": output["desire_holder_position"] == "feminine",
            "female_pleasure_visible": output["pleasure_holder_position"]
            == "feminine",
            "female_refusal_visible": output["refusal_resistance_position"]
            == "feminine",
            "female_speech_visible": output["attributed_speech_position"]
            == "feminine",
        }
        any_subjectivity = any(subjectivity.values())
        objectified = output["objectification_target_position"] == "feminine"
        return {
            **subjectivity,
            "any_female_position_visible": any_subjectivity,
            "female_objectification_visible": objectified,
            "objectification_and_all_four_positions_absent": objectified
            and not any_subjectivity,
        }

    for track in TRACKS:
        opportunity = [
            row
            for row in track_rows(valid, track)
            if row["output"]["distribution_visibility"] == "visible"
            and row["output"]["distribution_target_position"] == "feminine"
        ]
        group_specs: list[tuple[str, str, Callable[[dict[str, Any]], bool]]] = [
            ("all", "all", lambda row: True),
            (
                "actor_visibility",
                "actor_visible",
                lambda row: position_visible(
                    row["output"]["distribution_actor_position"]
                ),
            ),
            (
                "actor_visibility",
                "actor_not_visible",
                lambda row: not position_visible(
                    row["output"]["distribution_actor_position"]
                ),
            ),
        ]
        group_specs.extend(
            (
                "editorial_cue_present",
                cue,
                lambda row, selected=cue: selected
                in row["output"]["editorial_framing_cues"],
            )
            for cue in EDITORIAL_CUES
        )
        group_specs.extend(
            (
                "length",
                str(length),
                lambda row, selected=length: row["length_band"] == selected,
            )
            for length in sorted({row["length_band"] for row in opportunity})
        )
        metric_names = tuple(metrics(opportunity[0]["output"])) if opportunity else (
            "female_desire_visible",
            "female_pleasure_visible",
            "female_refusal_visible",
            "female_speech_visible",
            "any_female_position_visible",
            "female_objectification_visible",
            "objectification_and_all_four_positions_absent",
        )
        for dimension, level, predicate in group_specs:
            subset = [row for row in opportunity if predicate(row)]
            for metric in metric_names:
                positive = sum(metrics(row["output"])[metric] for row in subset)
                table.append(
                    {
                        "track": track,
                        "dimension": dimension,
                        "level": level,
                        "metric": metric,
                        "positive_n": positive,
                        "denominator_n": len(subset),
                        "rate": safe_rate(positive, len(subset)),
                    }
                )
        actor_visible = [
            row
            for row in opportunity
            if position_visible(row["output"]["distribution_actor_position"])
        ]
        actor_absent = [row for row in opportunity if row not in actor_visible]
        for metric in metric_names:
            first_positive = sum(metrics(row["output"])[metric] for row in actor_visible)
            second_positive = sum(metrics(row["output"])[metric] for row in actor_absent)
            odds, p_value = fisher_binary(
                first_positive,
                len(actor_visible),
                second_positive,
                len(actor_absent),
            )
            tests.append(
                {
                    "family": "E5_feminine_configuration",
                    "track": track,
                    "comparison": f"actor_visible_vs_not__{metric}",
                    "first_positive_n": first_positive,
                    "first_n": len(actor_visible),
                    "second_positive_n": second_positive,
                    "second_n": len(actor_absent),
                    "difference": safe_rate(first_positive, len(actor_visible))
                    - safe_rate(second_positive, len(actor_absent)),
                    "odds_ratio": odds,
                    "p_raw": p_value,
                }
            )
    return table


def control_heterogeneity_table(
    rows: list[dict[str, Any]], tests: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    valid = [row for row in rows if analytic_valid(row)]
    aliases = source_aliases(valid)
    table = []
    for track in ("probability", "mechanism"):
        base = [row for row in valid if row["sampling_track"] == track]
        variants: list[tuple[str, str, list[dict[str, Any]]]] = [
            ("main", "all", base),
            (
                "normalization",
                "no_normalization",
                [row for row in base if not row.get("normalization_actions")],
            ),
            (
                "normalization",
                "normalized",
                [row for row in base if row.get("normalization_actions")],
            ),
            (
                "evidence",
                "complete",
                [
                    row
                    for row in base
                    if (row.get("derived") or {}).get("evidence_complete")
                ],
            ),
            (
                "evidence",
                "incomplete",
                [
                    row
                    for row in base
                    if not (row.get("derived") or {}).get("evidence_complete")
                ],
            ),
        ]
        for cue in CONTEXT_CUES:
            variants.append(
                (
                    "context_present",
                    cue,
                    [row for row in base if cue in row["output"]["context_frame_codes"]],
                )
            )
        for form in CONTROL_FORMS:
            variants.append(
                (
                    "control_form_present",
                    form,
                    [row for row in base if form in row["output"]["control_form"]],
                )
            )
        for source, alias in aliases.items():
            variants.append(
                ("source", alias, [row for row in base if row["source_site"] == source])
            )
        for length in sorted({row["length_band"] for row in base}):
            variants.append(
                (
                    "length",
                    str(length),
                    [row for row in base if row["length_band"] == length],
                )
            )
        for dimension, level, subset in variants:
            contrast = exact_contrast(
                subset,
                lambda output: output["objectification_target_position"]
                == "feminine",
            )
            table.append(
                {
                    "track": track,
                    "dimension": dimension,
                    "level": level,
                    "analysis_n": len(subset),
                    **contrast,
                }
            )
            tests.append(
                {
                    "family": "E6_control_heterogeneity",
                    "track": track,
                    "comparison": f"{dimension}:{level}",
                    "first_positive_n": contrast["direct_positive_n"],
                    "first_n": contrast["direct_denominator_n"],
                    "second_positive_n": contrast["force_positive_n"],
                    "second_n": contrast["force_denominator_n"],
                    "difference": contrast[
                        "direct_minus_force_risk_difference"
                    ],
                    "odds_ratio": contrast["fisher_odds_ratio"],
                    "p_raw": contrast["fisher_two_sided_p"],
                }
            )
    return table


def generalized_firth_control(
    rows: list[dict[str, Any]], covariates: tuple[str, ...]
) -> dict[str, Any]:
    model_rows = [row for row in rows if control_group(row["output"]) is not None]
    levels = {
        covariate: sorted({str(row[covariate]) for row in model_rows})
        for covariate in covariates
    }
    design_rows = []
    for row in model_rows:
        dummies: list[int] = []
        for covariate in covariates:
            dummies.extend(
                int(str(row[covariate]) == level)
                for level in levels[covariate][1:]
            )
        design_rows.append(
            [
                1,
                int(
                    control_group(row["output"])
                    == "direct_control_without_explicit_nonconsent"
                ),
                *dummies,
            ]
        )
    design = np.asarray(design_rows, dtype=float)
    outcome = np.asarray(
        [
            row["output"]["objectification_target_position"] == "feminine"
            for row in model_rows
        ],
        dtype=float,
    )
    if len(model_rows) < design.shape[1] + 5 or len(set(outcome)) < 2:
        return {
            "analysis_n": len(model_rows),
            "parameters_n": design.shape[1],
            "force_probability": math.nan,
            "direct_probability": math.nan,
            "direct_minus_force_risk_difference": math.nan,
            "treatment_log_odds": math.nan,
        }
    try:
        fit = fit_firth(design, outcome)
    except RuntimeError:
        fit = fit_firth_lbfgs(design, outcome)
    force_design = design.copy()
    direct_design = design.copy()
    force_design[:, 1] = 0
    direct_design[:, 1] = 1
    force_probability = float(np.mean(expit(force_design @ fit["beta"])))
    direct_probability = float(np.mean(expit(direct_design @ fit["beta"])))
    return {
        "analysis_n": len(model_rows),
        "parameters_n": design.shape[1],
        "force_probability": force_probability,
        "direct_probability": direct_probability,
        "direct_minus_force_risk_difference": direct_probability
        - force_probability,
        "treatment_log_odds": float(fit["beta"][1]),
    }


def control_sampling_stratum_tables(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    valid = [
        row
        for row in rows
        if analytic_valid(row)
        and row["sampling_track"] == "mechanism"
        and control_group(row["output"]) is not None
    ]
    strata_table = []
    mh_num = 0.0
    mh_den = 0.0
    cmh_observed_minus_expected = 0.0
    cmh_variance = 0.0
    for stratum in sorted({row["sampling_stratum"] for row in valid}):
        subset = [row for row in valid if row["sampling_stratum"] == stratum]
        contrast = exact_contrast(
            subset,
            lambda output: output["objectification_target_position"] == "feminine",
        )
        a = int(contrast["direct_positive_n"])
        b = int(contrast["direct_denominator_n"]) - a
        c = int(contrast["force_positive_n"])
        d = int(contrast["force_denominator_n"]) - c
        n = a + b + c + d
        if n > 1 and a + b and c + d:
            mh_num += a * d / n
            mh_den += b * c / n
            expected = (a + b) * (a + c) / n
            variance = (a + b) * (c + d) * (a + c) * (b + d) / (
                n * n * (n - 1)
            )
            cmh_observed_minus_expected += a - expected
            cmh_variance += variance
        strata_table.append(
            {
                "sampling_stratum": stratum,
                **contrast,
            }
        )
    cmh_chi_square = (
        cmh_observed_minus_expected**2 / cmh_variance
        if cmh_variance
        else math.nan
    )
    adjustment_table = [
        {
            "method": "CMH_common_odds_ratio_by_sampling_stratum",
            "analysis_n": len(valid),
            "parameters_n": len({row["sampling_stratum"] for row in valid}),
            "force_probability": math.nan,
            "direct_probability": math.nan,
            "direct_minus_force_risk_difference": math.nan,
            "treatment_log_odds": math.nan,
            "common_odds_ratio": mh_num / mh_den if mh_den else math.nan,
            "test_statistic": cmh_chi_square,
            "p_value": float(chi2.sf(cmh_chi_square, 1))
            if math.isfinite(cmh_chi_square)
            else math.nan,
        }
    ]
    for label, covariates in (
        ("Firth_sampling_stratum", ("sampling_stratum",)),
        (
            "Firth_sampling_stratum_source_length",
            ("sampling_stratum", "source_site", "length_band"),
        ),
        (
            "Firth_sampling_stratum_source_length_split",
            ("sampling_stratum", "source_site", "length_band", "frozen_split"),
        ),
    ):
        adjustment_table.append(
            {
                "method": label,
                **generalized_firth_control(valid, covariates),
                "common_odds_ratio": math.nan,
                "test_statistic": math.nan,
                "p_value": math.nan,
            }
        )
    weighted_table = []
    for weight in ("w_record", "w_cluster"):
        rates = {}
        for group in (
            "direct_control_without_explicit_nonconsent",
            "explicit_force_nonconsent",
        ):
            subset = [row for row in valid if control_group(row["output"]) == group]
            weights = np.asarray([float(row[weight]) for row in subset])
            outcomes = np.asarray(
                [
                    row["output"]["objectification_target_position"] == "feminine"
                    for row in subset
                ],
                dtype=float,
            )
            rates[group] = (
                float(np.sum(weights * outcomes) / np.sum(weights))
                if len(subset)
                else math.nan
            )
        weighted_table.append(
            {
                "weight": weight,
                "direct_probability": rates[
                    "direct_control_without_explicit_nonconsent"
                ],
                "force_probability": rates["explicit_force_nonconsent"],
                "direct_minus_force_risk_difference": rates[
                    "direct_control_without_explicit_nonconsent"
                ]
                - rates["explicit_force_nonconsent"],
                "warning": (
                    "M-frame IPW is descriptive; zero-inclusion design cells limit "
                    "recovery of the full candidate frame"
                ),
            }
        )
    return strata_table, adjustment_table, weighted_table


def privacy_sampling_stratum_table(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    valid = [
        row
        for row in rows
        if analytic_valid(row) and row["sampling_track"] == "mechanism"
    ]
    table = []
    for stratum in sorted({row["sampling_stratum"] for row in valid}):
        subset = [row for row in valid if row["sampling_stratum"] == stratum]
        distribution = [
            row
            for row in subset
            if row["output"]["distribution_visibility"] == "visible"
        ]
        creation_distribution = [
            row
            for row in distribution
            if row["output"]["material_creation_visibility"] == "visible"
        ]
        specs = (
            (
                "distribution_target_vs_actor",
                distribution,
                lambda output: position_visible(
                    output["distribution_target_position"]
                ),
                lambda output: position_visible(
                    output["distribution_actor_position"]
                ),
            ),
            (
                "creation_actor_vs_distribution_actor",
                creation_distribution,
                lambda output: position_visible(
                    output["material_creation_actor_position"]
                ),
                lambda output: position_visible(
                    output["distribution_actor_position"]
                ),
            ),
        )
        for comparison, opportunity, first, second in specs:
            table.append(
                {
                    "sampling_stratum": stratum,
                    "comparison": comparison,
                    **paired_counts(opportunity, first, second),
                }
            )
    return table


def weighted_from_values(
    rows: list[dict[str, Any]],
    predicate: Callable[[dict[str, Any]], bool],
    weight: str,
    phase_factors: dict[tuple[str, str, str], float],
) -> tuple[float, float, float, float]:
    weights = np.asarray(
        [
            float(row[weight])
            * phase_factors[
                (
                    str(row["frozen_split"]),
                    str(row["source_site"]),
                    str(row["length_band"]),
                )
            ]
            for row in rows
        ],
        dtype=float,
    )
    outcomes = np.asarray([predicate(row["output"]) for row in rows], dtype=float)
    estimate = float(np.sum(weights * outcomes) / np.sum(weights))
    effective_n = float(np.sum(weights) ** 2 / np.sum(weights**2))
    standard_error = math.sqrt(
        max(estimate * (1 - estimate), 0.0) / effective_n
    )
    return (
        estimate,
        max(0.0, estimate - 1.96 * standard_error),
        min(1.0, estimate + 1.96 * standard_error),
        effective_n,
    )


def probability_wave_factors(
    scope: str,
) -> tuple[dict[tuple[str, str, str], float], int]:
    allowed = {
        "discovery": {"gate_0500", "discovery_1500"},
        "validation": {"validation_3000"},
        "full": {"gate_0500", "discovery_1500", "validation_3000"},
    }[scope]
    full_counts: Counter[tuple[str, str, str]] = Counter()
    selected_counts: Counter[tuple[str, str, str]] = Counter()
    with SAMPLE.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["sampling_track"] != "probability":
                continue
            cell = (row["frozen_split"], row["source_site"], row["length_band"])
            full_counts[cell] += 1
            if row["incremental_wave"] in allowed:
                selected_counts[cell] += 1
    zero_cells = sum(selected_counts[cell] == 0 for cell in full_counts)
    factors = {
        cell: full_count / selected_counts[cell]
        for cell, full_count in full_counts.items()
        if selected_counts[cell]
    }
    return factors, zero_cells


def weighted_privacy_table(
    rows: list[dict[str, Any]], scope: str
) -> list[dict[str, Any]]:
    probability = [
        row
        for row in rows
        if analytic_valid(row) and row["sampling_track"] == "probability"
    ]
    distribution = [
        row
        for row in probability
        if row["output"]["distribution_visibility"] == "visible"
    ]
    feminine_distribution = [
        row
        for row in distribution
        if row["output"]["distribution_target_position"] == "feminine"
    ]
    specs: tuple[
        tuple[str, list[dict[str, Any]], Callable[[dict[str, Any]], bool]], ...
    ] = (
        (
            "distribution_visible",
            probability,
            lambda output: output["distribution_visibility"] == "visible",
        ),
        (
            "feminine_target_given_distribution",
            distribution,
            lambda output: output["distribution_target_position"] == "feminine",
        ),
        (
            "feminine_or_mixed_target_given_distribution",
            distribution,
            lambda output: output["distribution_target_position"]
            in {"feminine", "mixed_multiple"},
        ),
        (
            "actor_visible_given_distribution",
            distribution,
            lambda output: position_visible(output["distribution_actor_position"]),
        ),
        (
            "target_visible_actor_not_given_distribution",
            distribution,
            lambda output: position_visible(output["distribution_target_position"])
            and not position_visible(output["distribution_actor_position"]),
        ),
        (
            "no_authorization_language_given_distribution",
            distribution,
            lambda output: output["distribution_authorization_visibility"]
            == "no_visible_authorization_language",
        ),
        (
            "female_objectification_given_feminine_distribution_target",
            feminine_distribution,
            lambda output: output["objectification_target_position"] == "feminine",
        ),
        (
            "any_female_position_given_feminine_distribution_target",
            feminine_distribution,
            lambda output: any(output[field] == "feminine" for field in SUBJECTIVITY),
        ),
        (
            "objectification_all_four_absent_given_feminine_distribution_target",
            feminine_distribution,
            lambda output: output["objectification_target_position"] == "feminine"
            and all(output[field] != "feminine" for field in SUBJECTIVITY),
        ),
    )
    phase_factors, zero_cells = probability_wave_factors(scope)
    table = []
    for metric, opportunity, predicate in specs:
        for estimand, weight in (("record", "w_record"), ("cluster", "w_cluster")):
            if opportunity:
                estimate, low, high, effective_n = weighted_ratio(
                    opportunity, predicate, weight
                )
                if zero_cells == 0:
                    phase_estimate, phase_low, phase_high, phase_effective_n = (
                        weighted_from_values(
                            opportunity, predicate, weight, phase_factors
                        )
                    )
                else:
                    phase_estimate = phase_low = phase_high = phase_effective_n = (
                        math.nan
                    )
            else:
                estimate = low = high = effective_n = math.nan
                phase_estimate = phase_low = phase_high = phase_effective_n = math.nan
            table.append(
                {
                    "metric": metric,
                    "estimand": estimand,
                    "opportunity_n": len(opportunity),
                    "weighted_estimate": estimate,
                    "approximate_95_low": low,
                    "approximate_95_high": high,
                    "kish_effective_n": effective_n,
                    "wave_adjusted_estimate": phase_estimate,
                    "wave_adjusted_approximate_95_low": phase_low,
                    "wave_adjusted_approximate_95_high": phase_high,
                    "wave_adjusted_kish_effective_n": phase_effective_n,
                    "wave_zero_inclusion_cells": zero_cells,
                    "warning": (
                        "Kish approximation is not final design-based variance; "
                        "unadjusted phase subsets omit wave-selection probability; "
                        "wave-adjusted estimate is unavailable when cells have zero inclusion"
                    ),
                }
            )
    return table


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scope", choices=("discovery", "validation", "full"), required=True
    )
    args = parser.parse_args()
    rows, input_path = load_rows(args.scope)
    expected = {"discovery": 2000, "validation": 3000, "full": 5000}[
        args.scope
    ]
    if len(rows) != expected:
        raise RuntimeError(f"{args.scope} has {len(rows)} rows; expected {expected}")
    tests: list[dict[str, Any]] = []
    authorization, authorization_realization = authorization_tables(rows, tests)
    control_strata, control_adjustment, control_weighted = (
        control_sampling_stratum_tables(rows)
    )
    outputs = {
        f"quality_by_group_{args.scope}.csv": quality_by_group(rows),
        f"normalization_action_profile_{args.scope}.csv": (
            normalization_action_profile(rows)
        ),
        f"sentinel_rates_{args.scope}.csv": sentinel_table(rows),
        f"substantive_sensitivity_{args.scope}.csv": substantive_sensitivity(rows),
        f"distribution_target_gender_{args.scope}.csv": distribution_gender_table(
            rows, tests
        ),
        f"authorization_actor_{args.scope}.csv": authorization,
        f"authorization_actor_realization_{args.scope}.csv": authorization_realization,
        f"editorial_distribution_{args.scope}.csv": editorial_distribution_table(
            rows, tests
        ),
        f"editorial_candidate_robustness_{args.scope}.csv": (
            editorial_candidate_robustness(rows)
        ),
        f"stage_transitions_{args.scope}.csv": stage_transition_table(rows),
        f"stage_persistence_robustness_{args.scope}.csv": (
            stage_persistence_robustness(rows)
        ),
        f"feminine_distribution_configuration_{args.scope}.csv": (
            feminine_distribution_configuration(rows, tests)
        ),
        f"control_heterogeneity_{args.scope}.csv": control_heterogeneity_table(
            rows, tests
        ),
        f"control_by_sampling_stratum_{args.scope}.csv": control_strata,
        f"control_sampling_stratum_adjustment_{args.scope}.csv": control_adjustment,
        f"control_mechanism_weighted_{args.scope}.csv": control_weighted,
        f"privacy_by_sampling_stratum_{args.scope}.csv": (
            privacy_sampling_stratum_table(rows)
        ),
        f"weighted_privacy_{args.scope}.csv": weighted_privacy_table(
            rows, args.scope
        ),
    }
    bh_adjust(tests)
    outputs[f"exploratory_tests_{args.scope}.csv"] = tests
    manifest_outputs = []
    for name, table in outputs.items():
        path = RESULTS / name
        write_csv(path, table)
        manifest_outputs.append(
            {
                "path": str(path.relative_to(ROOT)),
                "rows": len(table),
                "sha256": sha256(path),
            }
        )
    structural_valid_n = sum(row["valid"] for row in rows)
    analytic_valid_n = sum(analytic_valid(row) for row in rows)
    manifest_inputs = {
        str(input_path.relative_to(ROOT)): sha256(input_path),
        str(SAMPLE.relative_to(ROOT)): sha256(SAMPLE),
        str(PLAN.relative_to(ROOT)): sha256(PLAN),
        str(CODEBOOK.relative_to(ROOT)): sha256(CODEBOOK),
    }
    if args.scope in {"validation", "full"}:
        manifest_inputs[str(CANDIDATE_FREEZE.relative_to(ROOT))] = sha256(
            CANDIDATE_FREEZE
        )
    manifest = {
        "created_at": utc_now(),
        "status": "complete",
        "scope": args.scope,
        "analysis_status": "post_hoc_exploratory_audit",
        "records": len(rows),
        "structurally_valid": structural_valid_n,
        "analytic_valid": analytic_valid_n,
        "unresolved": len(rows) - structural_valid_n,
        "technical_noise": structural_valid_n - analytic_valid_n,
        "inputs": manifest_inputs,
        "outputs": manifest_outputs,
        "multiple_testing": "Benjamini-Hochberg within named exploratory family",
        "privacy": (
            "Aggregate outputs exclude titles, evidence spans, record identifiers, "
            "item indices, literal source names, and provider responses."
        ),
    }
    manifest_path = RESULTS / f"manifest_{args.scope}.json"
    write_json(manifest_path, manifest)
    print(
        json.dumps(
            {
                "scope": args.scope,
                "records": len(rows),
                "structurally_valid": structural_valid_n,
                "analytic_valid": analytic_valid_n,
                "tables": len(outputs),
                "tests": len(tests),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
