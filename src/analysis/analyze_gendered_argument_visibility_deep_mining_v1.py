"""Deep-mine the locked gendered-argument-visibility pilot.

The 480 records annotated under codebook v0.2 are the primary analysis set.
The 116 valid development records annotated under v0.1 are used only for
measurement-version diagnostics and directional replication.  All outputs are
aggregate and must not contain title text, identifiers, evidence spans, or
literal source-site names.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import brentq, minimize
from scipy.special import expit
from scipy.stats import binomtest, chi2, fisher_exact

ROOT = Path(__file__).resolve().parents[2]
INPUT = (
    ROOT
    / "data/interim/50_gendered_argument_visibility_600_v1/private/combined_records.jsonl"
)
INPUT_MANIFEST = ROOT / "data/interim/50_gendered_argument_visibility_600_v1/manifest.json"
SAMPLE = (
    ROOT
    / "data/interim/48_gendered_argument_visibility_pilot_v1/private/pilot_manifest.csv"
)
SAMPLE_MANIFEST = (
    ROOT / "data/interim/48_gendered_argument_visibility_pilot_v1/sample_manifest.json"
)
CODEBOOK_V01 = ROOT / "config/gendered_argument_visibility_codebook_v0_1.json"
CODEBOOK_V02 = ROOT / "config/gendered_argument_visibility_codebook_v0_2.json"
PROMPT_V01 = ROOT / "prompts/gendered_argument_visibility_annotation_v0_1.md"
PROMPT_V02 = ROOT / "prompts/gendered_argument_visibility_annotation_v0_2.md"
RESULTS = ROOT / "results/tables/gendered_argument_visibility_deep_mining_v1"

PRIMARY_PHASE = "locked"
DEVELOPMENT_PHASE = "development"
FORCE = "explicit_force_nonconsent"
DIRECT = "direct_control_without_explicit_nonconsent"
PRIVACY = "privacy_circulation"
VISIBLE_EVENT = {"explicit", "implied"}
VISIBLE_POSITIONS_EXCLUDE = {"not_visible", "unclear", "not_applicable"}
FEMININE_OR_MIXED = {"feminine", "mixed_multiple"}
SUBJECTIVITY_FIELDS = (
    "desire_holder_position",
    "pleasure_holder_position",
    "refusal_resistance_position",
    "attributed_speech_position",
)
MODEL_BOOTSTRAP_SEED = 20260815
MODEL_BOOTSTRAP_REPS = 999


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"refusing to write empty table: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0])
    if any(list(row) != fields for row in rows):
        raise ValueError(f"inconsistent columns in {path}")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def wilson(selected: int, denominator: int) -> tuple[float, float]:
    if denominator == 0:
        return math.nan, math.nan
    z = 1.959963984540054
    rate = selected / denominator
    scale = 1 + z * z / denominator
    center = (rate + z * z / (2 * denominator)) / scale
    half = (
        z
        * math.sqrt(
            rate * (1 - rate) / denominator
            + z * z / (4 * denominator * denominator)
        )
        / scale
    )
    return center - half, center + half


def position_visible(value: str) -> bool:
    return value not in VISIBLE_POSITIONS_EXCLUDE


def feminine_subjectivity_visible(
    output: dict[str, Any], *, include_mixed: bool = False
) -> bool:
    positions = FEMININE_OR_MIXED if include_mixed else {"feminine"}
    return any(output[field] in positions for field in SUBJECTIVITY_FIELDS)


def valid_phase(
    rows: list[dict[str, Any]], phase: str
) -> list[dict[str, Any]]:
    return [row for row in rows if row["valid"] and row["pilot_phase"] == phase]


def exact_comparison(
    selected_a: int, denominator_a: int, selected_b: int, denominator_b: int
) -> tuple[float, float, float]:
    if denominator_a == 0 or denominator_b == 0:
        return math.nan, math.nan, math.nan
    odds_ratio, p_value = fisher_exact(
        [
            [selected_a, denominator_a - selected_a],
            [selected_b, denominator_b - selected_b],
        ]
    )
    return (
        selected_a / denominator_a - selected_b / denominator_b,
        float(odds_ratio),
        float(p_value),
    )


def paired_counts(
    rows: Iterable[dict[str, Any]],
    first: Callable[[dict[str, Any]], bool],
    second: Callable[[dict[str, Any]], bool],
) -> dict[str, Any]:
    counts = Counter((first(row["output"]), second(row["output"])) for row in rows)
    first_only = counts[(True, False)]
    second_only = counts[(False, True)]
    discordant = first_only + second_only
    p_value = (
        float(binomtest(first_only, discordant, 0.5).pvalue)
        if discordant
        else 1.0
    )
    denominator = sum(counts.values())
    return {
        "denominator_n": denominator,
        "both_visible_n": counts[(True, True)],
        "first_only_n": first_only,
        "second_only_n": second_only,
        "neither_visible_n": counts[(False, False)],
        "first_minus_second_rate": (
            (first_only - second_only) / denominator if denominator else math.nan
        ),
        "exact_p": p_value,
    }


def proportion_metric(
    metric: str,
    phase: str,
    subset: list[dict[str, Any]],
    predicate: Callable[[dict[str, Any]], bool],
) -> dict[str, Any]:
    selected = sum(predicate(row["output"]) for row in subset)
    low, high = wilson(selected, len(subset))
    return {
        "metric": metric,
        "phase": phase,
        "selected_n": selected,
        "denominator_n": len(subset),
        "proportion": selected / len(subset) if subset else math.nan,
        "wilson_95_low": low,
        "wilson_95_high": high,
    }


def measurement_version_table(
    development: list[dict[str, Any]], locked: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    def distribution(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            row for row in rows if row["output"]["distribution_visibility"] == "visible"
        ]

    def nonconsent(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            row
            for row in rows
            if row["output"]["consent_visibility"]
            == "refusal_or_nonconsent_explicit"
        ]

    def female_control(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            row
            for row in rows
            if row["output"]["control_visibility"] in VISIBLE_EVENT
            and row["output"]["control_target_position"] == "feminine"
        ]

    specs = [
        (
            "distribution_actor_not_visible",
            distribution,
            lambda output: output["distribution_actor_position"] == "not_visible",
            "low",
        ),
        (
            "distribution_target_position_visible",
            distribution,
            lambda output: position_visible(output["distribution_target_position"]),
            "low",
        ),
        (
            "explicit_nonconsent_refusal_position_not_visible",
            nonconsent,
            lambda output: output["refusal_resistance_position"] == "not_visible",
            "medium",
        ),
        (
            "female_control_subjectivity_absent",
            female_control,
            lambda output: not feminine_subjectivity_visible(output),
            "high_semantic_definition_added_in_v0.2",
        ),
        (
            "female_control_objectification",
            female_control,
            lambda output: output["objectification_target_position"] == "feminine",
            "high_semantic_definition_added_in_v0.2",
        ),
    ]
    table: list[dict[str, Any]] = []
    for metric, selector, predicate, version_risk in specs:
        dev_subset = selector(development)
        locked_subset = selector(locked)
        dev_selected = sum(predicate(row["output"]) for row in dev_subset)
        locked_selected = sum(predicate(row["output"]) for row in locked_subset)
        difference, odds_ratio, p_value = exact_comparison(
            dev_selected,
            len(dev_subset),
            locked_selected,
            len(locked_subset),
        )
        table.append(
            {
                "metric": metric,
                "development_selected_n": dev_selected,
                "development_denominator_n": len(dev_subset),
                "development_proportion": dev_selected / len(dev_subset),
                "locked_selected_n": locked_selected,
                "locked_denominator_n": len(locked_subset),
                "locked_proportion": locked_selected / len(locked_subset),
                "development_minus_locked_risk_difference": difference,
                "odds_ratio_development_vs_locked": odds_ratio,
                "fisher_exact_two_sided_p": p_value,
                "measurement_version_risk": version_risk,
            }
        )
    return table


def privacy_tables(
    development: list[dict[str, Any]], locked: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    phases = [
        ("locked_v0.2_primary", locked),
        ("development_v0.1_replication", development),
        ("combined_supplementary", development + locked),
    ]
    profile: list[dict[str, Any]] = []
    paired: list[dict[str, Any]] = []
    for phase, phase_rows in phases:
        for scope, scoped_rows in (
            ("all_distribution_visible", phase_rows),
            (
                "privacy_sampling_stratum_distribution_visible",
                [row for row in phase_rows if row["sampling_stratum"] == PRIVACY],
            ),
            (
                "other_strata_distribution_visible",
                [row for row in phase_rows if row["sampling_stratum"] != PRIVACY],
            ),
        ):
            subset = [
                row
                for row in scoped_rows
                if row["output"]["distribution_visibility"] == "visible"
            ]
            if not subset:
                continue
            for metric, predicate in (
                (
                    "distribution_target_position_visible",
                    lambda output: position_visible(
                        output["distribution_target_position"]
                    ),
                ),
                (
                    "distribution_actor_position_visible",
                    lambda output: position_visible(output["distribution_actor_position"]),
                ),
                (
                    "distribution_actor_not_visible",
                    lambda output: output["distribution_actor_position"]
                    == "not_visible",
                ),
                (
                    "distribution_target_feminine",
                    lambda output: output["distribution_target_position"] == "feminine",
                ),
                (
                    "distribution_target_mixed_multiple",
                    lambda output: output["distribution_target_position"]
                    == "mixed_multiple",
                ),
                (
                    "distribution_target_gender_unspecified",
                    lambda output: output["distribution_target_position"]
                    == "gender_unspecified",
                ),
                (
                    "no_visible_authorization_language",
                    lambda output: output["distribution_authorization_visibility"]
                    == "no_visible_authorization_language",
                ),
                (
                    "unauthorized_explicit",
                    lambda output: output["distribution_authorization_visibility"]
                    == "unauthorized_explicit",
                ),
                (
                    "feminine_subjectivity_any",
                    feminine_subjectivity_visible,
                ),
            ):
                row = proportion_metric(metric, phase, subset, predicate)
                row["scope"] = scope
                profile.append(
                    {
                        "phase": row["phase"],
                        "scope": row["scope"],
                        "metric": row["metric"],
                        "selected_n": row["selected_n"],
                        "denominator_n": row["denominator_n"],
                        "proportion": row["proportion"],
                        "wilson_95_low": row["wilson_95_low"],
                        "wilson_95_high": row["wilson_95_high"],
                    }
                )
            test = paired_counts(
                subset,
                lambda output: position_visible(output["distribution_target_position"]),
                lambda output: position_visible(output["distribution_actor_position"]),
            )
            paired.append(
                {
                    "phase": phase,
                    "scope": scope,
                    "comparison": "target_position_visible_vs_actor_position_visible",
                    **test,
                }
            )

    relevant = [
        row
        for row in locked
        if row["output"]["privacy_chain_relevance"] == "relevant"
    ]
    stage_profiles = Counter(
        (
            row["output"]["material_creation_visibility"],
            row["output"]["acquisition_visibility"],
            row["output"]["distribution_visibility"],
            row["output"]["exposure_visibility"],
        )
        for row in relevant
    )
    profiles = [
        {
            "material_creation_visibility": key[0],
            "acquisition_visibility": key[1],
            "distribution_visibility": key[2],
            "exposure_visibility": key[3],
            "selected_n": selected,
            "denominator_n": len(relevant),
            "proportion": selected / len(relevant),
        }
        for key, selected in sorted(
            stage_profiles.items(), key=lambda item: (-item[1], item[0])
        )
    ]
    return profile, paired, profiles


def privacy_transition_table(
    development: list[dict[str, Any]], locked: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    table: list[dict[str, Any]] = []
    phases = [
        ("locked_v0.2_primary", locked),
        ("development_v0.1_replication", development),
        ("combined_supplementary", development + locked),
    ]
    for phase, phase_rows in phases:
        for scope, scoped in (
            ("all_privacy_relevant", phase_rows),
            (
                "privacy_sampling_stratum",
                [row for row in phase_rows if row["sampling_stratum"] == PRIVACY],
            ),
        ):
            creation_distribution = [
                row
                for row in scoped
                if row["output"]["privacy_chain_relevance"] == "relevant"
                and row["output"]["material_creation_visibility"] == "visible"
                and row["output"]["distribution_visibility"] == "visible"
            ]
            if not creation_distribution:
                continue
            test = paired_counts(
                creation_distribution,
                lambda output: position_visible(
                    output["material_creation_actor_position"]
                ),
                lambda output: position_visible(output["distribution_actor_position"]),
            )
            target_both_visible = sum(
                position_visible(row["output"]["material_creation_target_position"])
                and position_visible(row["output"]["distribution_target_position"])
                for row in creation_distribution
            )
            target_same = sum(
                row["output"]["material_creation_target_position"]
                == row["output"]["distribution_target_position"]
                for row in creation_distribution
            )
            target_feminine_or_mixed_both = sum(
                row["output"]["material_creation_target_position"]
                in FEMININE_OR_MIXED
                and row["output"]["distribution_target_position"]
                in FEMININE_OR_MIXED
                for row in creation_distribution
            )
            creation_distribution_exposure = [
                row
                for row in creation_distribution
                if row["output"]["exposure_visibility"] == "visible"
            ]
            table.append(
                {
                    "phase": phase,
                    "scope": scope,
                    "comparison": "creation_actor_visible_vs_distribution_actor_visible",
                    **test,
                    "target_both_stages_visible_n": target_both_visible,
                    "target_same_position_code_n": target_same,
                    "target_feminine_or_mixed_both_stages_n": (
                        target_feminine_or_mixed_both
                    ),
                    "creation_distribution_exposure_visible_n": len(
                        creation_distribution_exposure
                    ),
                    "three_stage_targets_all_visible_n": sum(
                        position_visible(
                            row["output"]["material_creation_target_position"]
                        )
                        and position_visible(
                            row["output"]["distribution_target_position"]
                        )
                        and position_visible(row["output"]["exposure_target_position"])
                        for row in creation_distribution_exposure
                    ),
                    "three_stage_targets_feminine_or_mixed_n": sum(
                        row["output"]["material_creation_target_position"]
                        in FEMININE_OR_MIXED
                        and row["output"]["distribution_target_position"]
                        in FEMININE_OR_MIXED
                        and row["output"]["exposure_target_position"]
                        in FEMININE_OR_MIXED
                        for row in creation_distribution_exposure
                    ),
                    "three_stage_distribution_actor_not_visible_n": sum(
                        row["output"]["distribution_actor_position"] == "not_visible"
                        for row in creation_distribution_exposure
                    ),
                }
            )
    return table


def nonconsent_table(
    development: list[dict[str, Any]], locked: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    table: list[dict[str, Any]] = []
    for phase, phase_rows in (
        ("locked_v0.2_primary", locked),
        ("development_v0.1_replication", development),
    ):
        subset = [
            row
            for row in phase_rows
            if row["output"]["consent_visibility"]
            == "refusal_or_nonconsent_explicit"
            and row["output"]["control_visibility"] in VISIBLE_EVENT
            and row["output"]["control_target_position"] == "feminine"
        ]
        for length_band in ["all"] + sorted(
            {row["length_band"] for row in subset}
        ):
            current = (
                subset
                if length_band == "all"
                else [row for row in subset if row["length_band"] == length_band]
            )
            neither_visible = sum(
                row["output"]["refusal_resistance_position"] == "not_visible"
                and row["output"]["attributed_speech_position"] == "not_visible"
                for row in current
            )
            neither_feminine = sum(
                row["output"]["refusal_resistance_position"] != "feminine"
                and row["output"]["attributed_speech_position"] != "feminine"
                for row in current
            )
            table.append(
                {
                    "phase": phase,
                    "length_band": length_band,
                    "explicit_nonconsent_feminine_control_target_n": len(current),
                    "feminine_refusal_resistance_visible_n": sum(
                        row["output"]["refusal_resistance_position"] == "feminine"
                        for row in current
                    ),
                    "feminine_attributed_speech_visible_n": sum(
                        row["output"]["attributed_speech_position"] == "feminine"
                        for row in current
                    ),
                    "neither_visible_refusal_nor_speech_n": neither_visible,
                    "neither_visible_refusal_nor_speech_rate": (
                        neither_visible / len(current) if current else math.nan
                    ),
                    "neither_feminine_refusal_nor_speech_n": neither_feminine,
                    "neither_feminine_refusal_nor_speech_rate": (
                        neither_feminine / len(current) if current else math.nan
                    ),
                    "feminine_subjectivity_all_four_absent_n": sum(
                        not feminine_subjectivity_visible(row["output"])
                        for row in current
                    ),
                }
            )
    return table


def control_phase_contrasts(
    development: list[dict[str, Any]], locked: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    table: list[dict[str, Any]] = []
    metrics = (
        (
            "feminine_control_target",
            lambda output: output["control_target_position"] == "feminine",
        ),
        (
            "control_actor_not_visible",
            lambda output: output["control_actor_position"] == "not_visible",
        ),
        (
            "feminine_objectification_target",
            lambda output: output["objectification_target_position"] == "feminine",
        ),
        (
            "feminine_subjectivity_absent",
            lambda output: not feminine_subjectivity_visible(output),
        ),
    )
    for phase, phase_rows in (
        ("locked_v0.2_primary", locked),
        ("development_v0.1_diagnostic", development),
    ):
        force = [
            row
            for row in phase_rows
            if row["sampling_stratum"] == FORCE
            and row["output"]["control_visibility"] in VISIBLE_EVENT
        ]
        direct = [
            row
            for row in phase_rows
            if row["sampling_stratum"] == DIRECT
            and row["output"]["control_visibility"] in VISIBLE_EVENT
        ]
        for metric, predicate in metrics:
            force_selected = sum(predicate(row["output"]) for row in force)
            direct_selected = sum(predicate(row["output"]) for row in direct)
            difference, odds_ratio, p_value = exact_comparison(
                direct_selected, len(direct), force_selected, len(force)
            )
            table.append(
                {
                    "phase": phase,
                    "metric": metric,
                    "direct_selected_n": direct_selected,
                    "direct_denominator_n": len(direct),
                    "direct_proportion": direct_selected / len(direct),
                    "force_selected_n": force_selected,
                    "force_denominator_n": len(force),
                    "force_proportion": force_selected / len(force),
                    "direct_minus_force_risk_difference": difference,
                    "odds_ratio_direct_vs_force": odds_ratio,
                    "fisher_exact_two_sided_p": p_value,
                }
            )
    return table


def composite_predicate(
    output: dict[str, Any], *, include_mixed_subjectivity: bool = False
) -> bool:
    return (
        output["objectification_target_position"] == "feminine"
        and not feminine_subjectivity_visible(
            output, include_mixed=include_mixed_subjectivity
        )
        and "erotic_promotional_approval" in output["editorial_framing_cues"]
    )


def control_configuration_table(
    development: list[dict[str, Any]],
    locked: list[dict[str, Any]],
    metadata: dict[int, dict[str, str]],
) -> list[dict[str, Any]]:
    table: list[dict[str, Any]] = []

    def eligible(rows: list[dict[str, Any]], stratum: str) -> list[dict[str, Any]]:
        return [
            row
            for row in rows
            if row["sampling_stratum"] == stratum
            and row["output"]["control_visibility"] in VISIBLE_EVENT
            and row["output"]["control_target_position"] == "feminine"
        ]

    specs: list[
        tuple[
            str,
            list[dict[str, Any]],
            Callable[[dict[str, Any]], bool],
            bool,
        ]
    ] = [
        ("locked_all", locked, lambda row: True, False),
        ("development_replication", development, lambda row: True, False),
        ("locked_all_mixed_subjectivity_sensitivity", locked, lambda row: True, True),
        (
            "locked_exclude_explicit_roleplay_bdsm",
            locked,
            lambda row: "explicit_roleplay_or_bdsm"
            not in row["output"]["context_frame_codes"],
            False,
        ),
        (
            "locked_exclude_old_objectification_overlap",
            locked,
            lambda row: "objectification_aggressive_framing"
            not in metadata[row["item_index"]]["overlap_flags"].split(";"),
            False,
        ),
    ]
    for sensitivity, phase_rows, filter_predicate, include_mixed in specs:
        force = [row for row in eligible(phase_rows, FORCE) if filter_predicate(row)]
        direct = [row for row in eligible(phase_rows, DIRECT) if filter_predicate(row)]
        force_selected = sum(
            composite_predicate(
                row["output"], include_mixed_subjectivity=include_mixed
            )
            for row in force
        )
        direct_selected = sum(
            composite_predicate(
                row["output"], include_mixed_subjectivity=include_mixed
            )
            for row in direct
        )
        difference, odds_ratio, p_value = exact_comparison(
            direct_selected, len(direct), force_selected, len(force)
        )
        table.append(
            {
                "sensitivity": sensitivity,
                "subjectivity_absence_definition": (
                    "no_feminine_or_mixed_position"
                    if include_mixed
                    else "no_strict_feminine_position"
                ),
                "direct_selected_n": direct_selected,
                "direct_denominator_n": len(direct),
                "direct_proportion": direct_selected / len(direct),
                "force_selected_n": force_selected,
                "force_denominator_n": len(force),
                "force_proportion": force_selected / len(force),
                "direct_minus_force_risk_difference": difference,
                "odds_ratio_direct_vs_force": odds_ratio,
                "fisher_exact_two_sided_p": p_value,
            }
        )
    return table


def control_female_target_profiles(
    development: list[dict[str, Any]], locked: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    table: list[dict[str, Any]] = []
    for phase, phase_rows in (
        ("locked_v0.2_primary", locked),
        ("development_v0.1_replication", development),
    ):
        for stratum in (FORCE, DIRECT):
            subset = [
                row
                for row in phase_rows
                if row["sampling_stratum"] == stratum
                and row["output"]["control_visibility"] in VISIBLE_EVENT
                and row["output"]["control_target_position"] == "feminine"
            ]
            table.append(
                {
                    "phase": phase,
                    "control_stratum": stratum,
                    "feminine_control_target_n": len(subset),
                    "feminine_objectification_n": sum(
                        row["output"]["objectification_target_position"] == "feminine"
                        for row in subset
                    ),
                    "objectification_and_subjectivity_absent_n": sum(
                        row["output"]["objectification_target_position"] == "feminine"
                        and not feminine_subjectivity_visible(row["output"])
                        for row in subset
                    ),
                    "objectification_subjectivity_absent_promotional_n": sum(
                        composite_predicate(row["output"]) for row in subset
                    ),
                    "neither_visible_refusal_nor_speech_n": sum(
                        row["output"]["refusal_resistance_position"] == "not_visible"
                        and row["output"]["attributed_speech_position"] == "not_visible"
                        for row in subset
                    ),
                    "feminine_subjectivity_all_four_absent_n": sum(
                        not feminine_subjectivity_visible(row["output"])
                        for row in subset
                    ),
                    "erotic_promotional_frame_n": sum(
                        "erotic_promotional_approval"
                        in row["output"]["editorial_framing_cues"]
                        for row in subset
                    ),
                }
            )
    return table


def nonconsent_specificity_table(
    development: list[dict[str, Any]], locked: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    table: list[dict[str, Any]] = []
    for phase, phase_rows in (
        ("locked_v0.2_primary", locked),
        ("development_v0.1_replication", development),
    ):
        female_control = [
            row
            for row in phase_rows
            if row["output"]["control_visibility"] in VISIBLE_EVENT
            and row["output"]["control_target_position"] == "feminine"
        ]
        for group, subset in (
            (
                "explicit_nonconsent",
                [
                    row
                    for row in female_control
                    if row["output"]["consent_visibility"]
                    == "refusal_or_nonconsent_explicit"
                ],
            ),
            (
                "other_consent_visibility",
                [
                    row
                    for row in female_control
                    if row["output"]["consent_visibility"]
                    != "refusal_or_nonconsent_explicit"
                ],
            ),
        ):
            absent = sum(
                row["output"]["refusal_resistance_position"] == "not_visible"
                and row["output"]["attributed_speech_position"] == "not_visible"
                for row in subset
            )
            table.append(
                {
                    "phase": phase,
                    "consent_group": group,
                    "selected_n": absent,
                    "denominator_n": len(subset),
                    "no_visible_refusal_or_speech_rate": absent / len(subset),
                }
            )
    return table


def subjectivity_diagnostics(locked: list[dict[str, Any]]) -> list[dict[str, Any]]:
    female_control = [
        row
        for row in locked
        if row["output"]["control_visibility"] in VISIBLE_EVENT
        and row["output"]["control_target_position"] == "feminine"
    ]
    table: list[dict[str, Any]] = []
    for length in sorted({row["length_band"] for row in female_control}):
        subset = [row for row in female_control if row["length_band"] == length]
        selected = sum(
            not feminine_subjectivity_visible(row["output"]) for row in subset
        )
        table.append(
            {
                "diagnostic": "subjectivity_absence_by_length",
                "group_a": length,
                "group_a_selected_n": selected,
                "group_a_denominator_n": len(subset),
                "group_b": "",
                "group_b_selected_n": "",
                "group_b_denominator_n": "",
                "group_a_minus_group_b_risk_difference": "",
                "fisher_exact_two_sided_p": "",
            }
        )
    for diagnostic, predicate in (
        (
            "subjectivity_absence_by_control_actor_visibility",
            lambda output: output["control_actor_position"] == "not_visible",
        ),
        (
            "subjectivity_absence_by_objectification",
            lambda output: output["objectification_target_position"] == "feminine",
        ),
    ):
        group_a = [row for row in female_control if predicate(row["output"])]
        group_b = [row for row in female_control if not predicate(row["output"])]
        selected_a = sum(
            not feminine_subjectivity_visible(row["output"]) for row in group_a
        )
        selected_b = sum(
            not feminine_subjectivity_visible(row["output"]) for row in group_b
        )
        difference, _, p_value = exact_comparison(
            selected_a, len(group_a), selected_b, len(group_b)
        )
        table.append(
            {
                "diagnostic": diagnostic,
                "group_a": "present_or_not_visible_focal_condition",
                "group_a_selected_n": selected_a,
                "group_a_denominator_n": len(group_a),
                "group_b": "comparison_condition",
                "group_b_selected_n": selected_b,
                "group_b_denominator_n": len(group_b),
                "group_a_minus_group_b_risk_difference": difference,
                "fisher_exact_two_sided_p": p_value,
            }
        )
    return table


def penalized_log_likelihood(
    beta: np.ndarray, design: np.ndarray, outcome: np.ndarray
) -> float:
    probability = expit(design @ beta)
    weights = probability * (1 - probability)
    information = design.T @ (weights[:, None] * design)
    sign, log_determinant = np.linalg.slogdet(information)
    if sign <= 0:
        return -math.inf
    likelihood = np.sum(
        outcome * np.log(np.clip(probability, 1e-15, 1))
        + (1 - outcome) * np.log(np.clip(1 - probability, 1e-15, 1))
    )
    return float(likelihood + 0.5 * log_determinant)


def fit_firth(
    design: np.ndarray,
    outcome: np.ndarray,
    *,
    start: np.ndarray | None = None,
    fixed: dict[int, float] | None = None,
    tolerance: float = 1e-7,
    max_iterations: int = 3000,
) -> dict[str, Any]:
    fixed = fixed or {}
    beta = (
        np.zeros(design.shape[1], dtype=float)
        if start is None
        else np.array(start, dtype=float, copy=True)
    )
    for index, value in fixed.items():
        beta[index] = value
    free = [index for index in range(design.shape[1]) if index not in fixed]
    current = penalized_log_likelihood(beta, design, outcome)
    for iteration in range(1, max_iterations + 1):
        probability = expit(design @ beta)
        weights = np.clip(probability * (1 - probability), 1e-12, None)
        information = design.T @ (weights[:, None] * design)
        information_inverse = np.linalg.pinv(information)
        leverage = weights * np.einsum(
            "ij,jk,ik->i", design, information_inverse, design
        )
        adjusted_score = design.T @ (
            outcome - probability + leverage * (0.5 - probability)
        )
        step = np.zeros_like(beta)
        free_information = information[np.ix_(free, free)]
        step[free] = np.linalg.pinv(free_information) @ adjusted_score[free]
        largest = float(np.max(np.abs(step[free]))) if free else 0.0
        if largest > 4:
            step *= 4 / largest
        scale = 1.0
        while scale > 1e-12:
            candidate = beta + scale * step
            for index, value in fixed.items():
                candidate[index] = value
            candidate_likelihood = penalized_log_likelihood(
                candidate, design, outcome
            )
            if candidate_likelihood >= current - 1e-12:
                break
            scale /= 2
        if scale <= 1e-12:
            raise RuntimeError("Firth step-halving failed")
        beta = candidate
        current = candidate_likelihood
        if not free or float(np.max(np.abs(scale * step[free]))) < tolerance:
            probability = expit(design @ beta)
            weights = np.clip(probability * (1 - probability), 1e-12, None)
            information = design.T @ (weights[:, None] * design)
            information_inverse = np.linalg.pinv(information)
            leverage = weights * np.einsum(
                "ij,jk,ik->i", design, information_inverse, design
            )
            adjusted_score = design.T @ (
                outcome - probability + leverage * (0.5 - probability)
            )
            return {
                "beta": beta,
                "penalized_log_likelihood": current,
                "iterations": iteration,
                "max_abs_adjusted_score": float(np.max(np.abs(adjusted_score[free])))
                if free
                else 0.0,
                "information_condition_number": float(np.linalg.cond(information)),
                "max_leverage": float(np.max(leverage)),
            }
    raise RuntimeError("Firth model did not converge")


def fit_firth_lbfgs(
    design: np.ndarray, outcome: np.ndarray
) -> dict[str, Any]:
    """Bounded optimizer fallback for separated bootstrap resamples."""
    result = minimize(
        lambda beta: -penalized_log_likelihood(beta, design, outcome),
        np.zeros(design.shape[1], dtype=float),
        method="L-BFGS-B",
        bounds=[(-30.0, 30.0)] * design.shape[1],
        options={"maxiter": 3000, "ftol": 1e-13, "gtol": 1e-7, "maxls": 50},
    )
    if not result.success or not np.all(np.isfinite(result.x)):
        raise RuntimeError(f"Firth L-BFGS fallback failed: {result.message}")
    return {
        "beta": np.asarray(result.x, dtype=float),
        "penalized_log_likelihood": float(-result.fun),
        "iterations": int(result.nit),
    }


def profile_interval(
    design: np.ndarray,
    outcome: np.ndarray,
    fit: dict[str, Any],
    coefficient_index: int,
) -> tuple[float, float, float]:
    target = fit["penalized_log_likelihood"] - chi2.ppf(0.95, 1) / 2
    center = float(fit["beta"][coefficient_index])

    def objective(value: float) -> float:
        constrained = fit_firth(
            design,
            outcome,
            start=fit["beta"],
            fixed={coefficient_index: value},
        )
        return float(constrained["penalized_log_likelihood"] - target)

    roots: list[float] = []
    for direction in (-1.0, 1.0):
        step = 0.5
        for _ in range(20):
            edge = center + direction * step
            if objective(edge) < 0:
                lower, upper = (edge, center) if direction < 0 else (center, edge)
                roots.append(float(brentq(objective, lower, upper, xtol=1e-8)))
                break
            step *= 2
        else:
            raise RuntimeError("could not bracket Firth profile interval")
    null_fit = fit_firth(
        design,
        outcome,
        start=fit["beta"],
        fixed={coefficient_index: 0.0},
    )
    likelihood_ratio = 2 * (
        fit["penalized_log_likelihood"]
        - null_fit["penalized_log_likelihood"]
    )
    return roots[0], roots[1], float(chi2.sf(likelihood_ratio, 1))


def control_design(
    rows: list[dict[str, Any]],
    source_levels: list[str],
    length_levels: list[str],
    treatment: int | None = None,
) -> np.ndarray:
    return np.asarray(
        [
            [
                1,
                (
                    int(row["sampling_stratum"] == DIRECT)
                    if treatment is None
                    else treatment
                ),
                *[
                    int(row["source_site"] == source)
                    for source in source_levels[1:]
                ],
                *[
                    int(row["length_band"] == length)
                    for length in length_levels[1:]
                ],
            ]
            for row in rows
        ],
        dtype=float,
    )


def control_objectification_model(
    locked: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    model_rows = [
        row
        for row in locked
        if row["sampling_stratum"] in {FORCE, DIRECT}
        and row["output"]["control_visibility"] in VISIBLE_EVENT
    ]
    source_levels = sorted({row["source_site"] for row in model_rows})
    length_levels = sorted({row["length_band"] for row in model_rows})
    design = control_design(model_rows, source_levels, length_levels)
    outcome = np.asarray(
        [
            row["output"]["objectification_target_position"] == "feminine"
            for row in model_rows
        ],
        dtype=float,
    )
    fit = fit_firth(design, outcome)
    lower, upper, profile_p = profile_interval(design, outcome, fit, 1)
    force_design = control_design(
        model_rows, source_levels, length_levels, treatment=0
    )
    direct_design = control_design(
        model_rows, source_levels, length_levels, treatment=1
    )
    force_probability = float(np.mean(expit(force_design @ fit["beta"])))
    direct_probability = float(np.mean(expit(direct_design @ fit["beta"])))

    rng = np.random.default_rng(MODEL_BOOTSTRAP_SEED)
    force_indices = np.flatnonzero(design[:, 1] == 0)
    direct_indices = np.flatnonzero(design[:, 1] == 1)
    bootstrap_differences: list[float] = []
    failures = 0
    fallbacks = 0
    for _ in range(MODEL_BOOTSTRAP_REPS):
        indices = np.concatenate(
            [
                rng.choice(force_indices, len(force_indices), replace=True),
                rng.choice(direct_indices, len(direct_indices), replace=True),
            ]
        )
        try:
            boot_rows = [model_rows[int(index)] for index in indices]
            boot_sources = sorted({row["source_site"] for row in boot_rows})
            boot_lengths = sorted({row["length_band"] for row in boot_rows})
            boot_design = control_design(boot_rows, boot_sources, boot_lengths)
            boot_outcome = outcome[indices]
            try:
                boot_fit = fit_firth(boot_design, boot_outcome)
            except RuntimeError:
                boot_fit = fit_firth_lbfgs(boot_design, boot_outcome)
                fallbacks += 1
            boot_force = boot_design.copy()
            boot_direct = boot_design.copy()
            boot_force[:, 1] = 0
            boot_direct[:, 1] = 1
            bootstrap_differences.append(
                float(
                    np.mean(expit(boot_direct @ boot_fit["beta"]))
                    - np.mean(expit(boot_force @ boot_fit["beta"]))
                )
            )
        except RuntimeError:
            failures += 1
    if not bootstrap_differences:
        raise RuntimeError("all Firth bootstrap replicates failed")
    bootstrap_low, bootstrap_high = np.percentile(
        bootstrap_differences, [2.5, 97.5]
    )

    common_cells: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in model_rows:
        common_cells[(row["source_site"], row["length_band"])].append(row)
    numerator = 0.0
    denominator = 0.0
    score = 0.0
    variance = 0.0
    common_support_n = 0
    common_support_cells = 0
    for cell in common_cells.values():
        direct = [row for row in cell if row["sampling_stratum"] == DIRECT]
        force = [row for row in cell if row["sampling_stratum"] == FORCE]
        if not direct or not force:
            continue
        common_support_cells += 1
        common_support_n += len(cell)
        a = sum(
            row["output"]["objectification_target_position"] == "feminine"
            for row in direct
        )
        b = len(direct) - a
        c = sum(
            row["output"]["objectification_target_position"] == "feminine"
            for row in force
        )
        d = len(force) - c
        n = a + b + c + d
        numerator += a * d / n
        denominator += b * c / n
        expected = (a + b) * (a + c) / n
        cell_variance = (
            (a + b) * (c + d) * (a + c) * (b + d) / (n * n * (n - 1))
        )
        score += a - expected
        variance += cell_variance
    cmh_chi_square = score * score / variance

    source_round_pairs = {
        (row["source_site"], row["data_round"]) for row in model_rows
    }
    unique_rounds_per_source = Counter()
    for source, _ in source_round_pairs:
        unique_rounds_per_source[source] = len(
            {round_name for current, round_name in source_round_pairs if current == source}
        )
    return [
        {
            "model": "firth_objectification_source_and_length",
            "analysis_n": len(model_rows),
            "outcome_positive_n": int(np.sum(outcome)),
            "parameters_n": design.shape[1],
            "design_rank": int(np.linalg.matrix_rank(design)),
            "direct_coefficient": float(fit["beta"][1]),
            "direct_odds_ratio": float(math.exp(fit["beta"][1])),
            "profile_95_or_low": float(math.exp(lower)),
            "profile_95_or_high": float(math.exp(upper)),
            "profile_likelihood_p": profile_p,
            "standardized_force_probability": force_probability,
            "standardized_direct_probability": direct_probability,
            "standardized_direct_minus_force_risk_difference": (
                direct_probability - force_probability
            ),
            "bootstrap_reps_requested": MODEL_BOOTSTRAP_REPS,
            "bootstrap_reps_successful": len(bootstrap_differences),
            "bootstrap_optimizer_fallbacks": fallbacks,
            "bootstrap_failures": failures,
            "bootstrap_risk_difference_95_low": float(bootstrap_low),
            "bootstrap_risk_difference_95_high": float(bootstrap_high),
            "bootstrap_seed": MODEL_BOOTSTRAP_SEED,
            "iterations": fit["iterations"],
            "max_abs_adjusted_score": fit["max_abs_adjusted_score"],
            "information_condition_number": fit["information_condition_number"],
            "max_leverage": fit["max_leverage"],
            "source_determines_round": all(
                count == 1 for count in unique_rounds_per_source.values()
            ),
            "cmh_source_by_length_common_support_n": common_support_n,
            "cmh_source_by_length_common_cells_n": common_support_cells,
            "cmh_source_by_length_odds_ratio": numerator / denominator,
            "cmh_source_by_length_p": float(chi2.sf(cmh_chi_square, 1)),
        }
    ]


def robustness_table(
    locked: list[dict[str, Any]],
    metadata: dict[int, dict[str, str]],
) -> list[dict[str, Any]]:
    public_sites = {
        site: f"source_{index:02d}"
        for index, site in enumerate(
            sorted({row["source_site"] for row in locked}), start=1
        )
    }

    def privacy_actor(rows: list[dict[str, Any]]) -> tuple[int, int, int, int, float]:
        subset = [
            row
            for row in rows
            if row["sampling_stratum"] == PRIVACY
            and row["output"]["distribution_visibility"] == "visible"
        ]
        selected = sum(
            row["output"]["distribution_actor_position"] == "not_visible"
            for row in subset
        )
        return selected, len(subset), 0, 0, selected / len(subset)

    def privacy_stage_transition(
        rows: list[dict[str, Any]],
    ) -> tuple[int, int, int, int, float]:
        subset = [
            row
            for row in rows
            if row["sampling_stratum"] == PRIVACY
            and row["output"]["privacy_chain_relevance"] == "relevant"
            and row["output"]["material_creation_visibility"] == "visible"
            and row["output"]["distribution_visibility"] == "visible"
        ]
        distribution_missing = sum(
            row["output"]["distribution_actor_position"] == "not_visible"
            for row in subset
        )
        creation_missing = sum(
            row["output"]["material_creation_actor_position"] == "not_visible"
            for row in subset
        )
        estimate = (
            (distribution_missing - creation_missing) / len(subset)
            if subset
            else math.nan
        )
        return (
            distribution_missing,
            len(subset),
            creation_missing,
            len(subset),
            estimate,
        )

    def nonconsent_voice(rows: list[dict[str, Any]]) -> tuple[int, int, int, int, float]:
        subset = [
            row
            for row in rows
            if row["output"]["consent_visibility"]
            == "refusal_or_nonconsent_explicit"
            and row["output"]["control_visibility"] in VISIBLE_EVENT
            and row["output"]["control_target_position"] == "feminine"
        ]
        selected = sum(
            row["output"]["refusal_resistance_position"] == "not_visible"
            and row["output"]["attributed_speech_position"] == "not_visible"
            for row in subset
        )
        return selected, len(subset), 0, 0, selected / len(subset)

    def objectification_difference(
        rows: list[dict[str, Any]],
    ) -> tuple[int, int, int, int, float]:
        force = [
            row
            for row in rows
            if row["sampling_stratum"] == FORCE
            and row["output"]["control_visibility"] in VISIBLE_EVENT
        ]
        direct = [
            row
            for row in rows
            if row["sampling_stratum"] == DIRECT
            and row["output"]["control_visibility"] in VISIBLE_EVENT
        ]
        force_selected = sum(
            row["output"]["objectification_target_position"] == "feminine"
            for row in force
        )
        direct_selected = sum(
            row["output"]["objectification_target_position"] == "feminine"
            for row in direct
        )
        estimate = direct_selected / len(direct) - force_selected / len(force)
        return direct_selected, len(direct), force_selected, len(force), estimate

    def composite_difference(
        rows: list[dict[str, Any]],
    ) -> tuple[int, int, int, int, float]:
        groups: list[tuple[int, int]] = []
        for stratum in (DIRECT, FORCE):
            subset = [
                row
                for row in rows
                if row["sampling_stratum"] == stratum
                and row["output"]["control_visibility"] in VISIBLE_EVENT
                and row["output"]["control_target_position"] == "feminine"
            ]
            groups.append(
                (
                    sum(composite_predicate(row["output"]) for row in subset),
                    len(subset),
                )
            )
        estimate = groups[0][0] / groups[0][1] - groups[1][0] / groups[1][1]
        return groups[0][0], groups[0][1], groups[1][0], groups[1][1], estimate

    metrics = {
        "privacy_stratum_distribution_actor_not_visible": privacy_actor,
        "privacy_stratum_distribution_minus_creation_actor_missing": (
            privacy_stage_transition
        ),
        "explicit_nonconsent_feminine_target_no_refusal_or_speech": nonconsent_voice,
        "direct_minus_force_feminine_objectification": objectification_difference,
        "direct_minus_force_objectification_subjectivity_absent_promotional": (
            composite_difference
        ),
    }
    filters: list[
        tuple[str, str, Callable[[dict[str, Any]], bool]]
    ] = [("baseline", "none", lambda row: True)]
    filters.extend(
        (
            "leave_one_source_out",
            public_sites[site],
            lambda row, site=site: row["source_site"] != site,
        )
        for site in sorted(public_sites)
    )
    filters.extend(
        (
            "leave_one_length_band_out",
            length,
            lambda row, length=length: row["length_band"] != length,
        )
        for length in sorted({row["length_band"] for row in locked})
    )
    filters.extend(
        (
            "retain_data_round",
            round_name,
            lambda row, round_name=round_name: row["data_round"] == round_name,
        )
        for round_name in sorted({row["data_round"] for row in locked})
    )
    filters.extend(
        [
            (
                "quality_subset",
                "evidence_rows_valid",
                lambda row: row["derived"]["evidence_rows_valid"],
            ),
            (
                "quality_subset",
                "evidence_complete",
                lambda row: row["derived"]["evidence_complete"],
            ),
            (
                "selection_echo_sensitivity",
                "exclude_old_objectification_overlap",
                lambda row: "objectification_aggressive_framing"
                not in metadata[row["item_index"]]["overlap_flags"].split(";"),
            ),
        ]
    )
    table: list[dict[str, Any]] = []
    for dimension, level, predicate in filters:
        retained = [row for row in locked if predicate(row)]
        for metric, estimator in metrics.items():
            selected_a, denominator_a, selected_b, denominator_b, estimate = estimator(
                retained
            )
            table.append(
                {
                    "metric": metric,
                    "robustness_dimension": dimension,
                    "level": level,
                    "group_a_selected_n": selected_a,
                    "group_a_denominator_n": denominator_a,
                    "group_b_selected_n": selected_b if denominator_b else "",
                    "group_b_denominator_n": denominator_b if denominator_b else "",
                    "estimate": estimate,
                }
            )
    return table


def main() -> None:
    all_rows = read_jsonl(INPUT)
    development = valid_phase(all_rows, DEVELOPMENT_PHASE)
    locked = valid_phase(all_rows, PRIMARY_PHASE)
    if len(all_rows) != 600 or len(development) != 116 or len(locked) != 480:
        raise RuntimeError("unexpected analysis population counts")
    with SAMPLE.open(encoding="utf-8", newline="") as handle:
        metadata = {
            int(row["item_index"]): row for row in csv.DictReader(handle)
        }

    RESULTS.mkdir(parents=True, exist_ok=True)
    outputs: list[tuple[Path, list[dict[str, Any]]]] = []
    privacy_profile, privacy_paired, privacy_profiles = privacy_tables(
        development, locked
    )
    outputs.extend(
        [
            (
                RESULTS / "measurement_version_audit.csv",
                measurement_version_table(development, locked),
            ),
            (RESULTS / "privacy_distribution_profile.csv", privacy_profile),
            (RESULTS / "privacy_argument_paired_tests.csv", privacy_paired),
            (RESULTS / "privacy_stage_profiles_locked.csv", privacy_profiles),
            (
                RESULTS / "privacy_stage_actor_transitions.csv",
                privacy_transition_table(development, locked),
            ),
            (
                RESULTS / "nonconsent_voice_by_length.csv",
                nonconsent_table(development, locked),
            ),
            (
                RESULTS / "control_phase_contrasts.csv",
                control_phase_contrasts(development, locked),
            ),
            (
                RESULTS / "control_configuration_contrasts.csv",
                control_configuration_table(development, locked, metadata),
            ),
            (
                RESULTS / "control_female_target_profiles.csv",
                control_female_target_profiles(development, locked),
            ),
            (
                RESULTS / "control_objectification_firth_model.csv",
                control_objectification_model(locked),
            ),
            (
                RESULTS / "nonconsent_specificity.csv",
                nonconsent_specificity_table(development, locked),
            ),
            (
                RESULTS / "subjectivity_diagnostics.csv",
                subjectivity_diagnostics(locked),
            ),
            (
                RESULTS / "robustness_checks.csv",
                robustness_table(locked, metadata),
            ),
        ]
    )
    row_counts: dict[str, int] = {}
    for path, table in outputs:
        write_csv(path, table)
        row_counts[str(path.relative_to(ROOT))] = len(table)

    inputs = [
        INPUT,
        INPUT_MANIFEST,
        SAMPLE,
        SAMPLE_MANIFEST,
        CODEBOOK_V01,
        CODEBOOK_V02,
        PROMPT_V01,
        PROMPT_V02,
    ]
    script = Path(__file__).resolve()
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete",
        "analysis_version": "gendered_argument_visibility_deep_mining_v1",
        "primary_analysis_population": "480 locked records annotated under v0.2",
        "development_diagnostic_population": (
            "116 valid development records annotated under v0.1"
        ),
        "counts": {
            "combined_records": len(all_rows),
            "locked_v0.2_primary": len(locked),
            "development_v0.1_valid_diagnostic": len(development),
            "development_unresolved_excluded": 4,
        },
        "parameters": {
            "model": "Firth logistic regression",
            "model_covariates": ["source_site", "length_band"],
            "bootstrap_seed": MODEL_BOOTSTRAP_SEED,
            "bootstrap_reps": MODEL_BOOTSTRAP_REPS,
            "firth_convergence_tolerance": 1e-7,
            "firth_max_iterations": 3000,
            "bootstrap_optimizer_fallback": "bounded L-BFGS-B",
            "primary_subjectivity_position": "strict feminine",
            "mixed_multiple_subjectivity": "reported as sensitivity",
        },
        "sampling_warning": (
            "Six equal information-enriched strata and one record per expression "
            "cluster; estimates describe sampled configurations, not 66,016-title "
            "population prevalence."
        ),
        "measurement_warning": (
            "v0.2 added exclusive semantic definitions for desire, pleasure, and "
            "objectification; v0.1 and v0.2 must not be pooled for those outcomes."
        ),
        "causal_warning": (
            "All results concern visible title wording; no causal, film-content, "
            "authorization-state, intention, audience-effect, or lived-experience "
            "claim is identified."
        ),
        "inputs": {
            str(path.relative_to(ROOT)): sha256_file(path) for path in inputs
        },
        "implementation": {
            "path": str(script.relative_to(ROOT)),
            "sha256": sha256_file(script),
        },
        "outputs": [
            {
                "path": str(path.relative_to(ROOT)),
                "rows": row_counts[str(path.relative_to(ROOT))],
                "sha256": sha256_file(path),
            }
            for path, _ in outputs
        ],
        "privacy": (
            "Aggregate outputs exclude titles, evidence spans, identifiers, item "
            "indices, literal source-site names, and provider responses."
        ),
    }
    (RESULTS / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "primary_n": len(locked),
                "diagnostic_n": len(development),
                "outputs": len(outputs),
                "results": str(RESULTS.relative_to(ROOT)),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
