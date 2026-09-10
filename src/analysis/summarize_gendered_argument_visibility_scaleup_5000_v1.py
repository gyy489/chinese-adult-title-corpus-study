"""Aggregate discovery, validation, and full v0.3 scale-up results.

Public outputs contain no titles, evidence spans, identifiers, item indices, or
literal source-site names.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from scipy.special import expit
from scipy.stats import binomtest, fisher_exact

from src.analysis.analyze_gendered_argument_visibility_deep_mining_v1 import (
    fit_firth,
    fit_firth_lbfgs,
)

ROOT = Path(__file__).resolve().parents[2]
RUN_DIR = ROOT / "data/interim/51_gendered_argument_visibility_scaleup_5000_v1"
SAMPLE = RUN_DIR / "private/sample_manifest.csv"
SAMPLE_MANIFEST = RUN_DIR / "sample_manifest.json"
FINAL_V1 = ROOT / "data/interim/44_deepseek_normalized_v1/private/final_records.jsonl"
RESULTS = ROOT / "results/tables/gendered_argument_visibility_scaleup_5000_v1"
VISIBLE = {"explicit", "implied"}
SUBJECTIVITY = (
    "desire_holder_position",
    "pleasure_holder_position",
    "refusal_resistance_position",
    "attributed_speech_position",
)
DIRECT_FORMS = {
    "restraint_or_confinement",
    "command_or_discipline",
    "threat",
    "deception_or_incapacity",
    "authority_or_economic_leverage",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise RuntimeError(f"refusing to write empty table: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def position_visible(value: str) -> bool:
    return value not in {"not_visible", "unclear", "not_applicable"}


def control_group(output: dict[str, Any]) -> str | None:
    if output["control_visibility"] not in VISIBLE:
        return None
    forms = set(output["control_form"])
    if (
        output["consent_visibility"] == "refusal_or_nonconsent_explicit"
        or "overt_force" in forms
    ):
        return "explicit_force_nonconsent"
    if forms & DIRECT_FORMS:
        return "direct_control_without_explicit_nonconsent"
    return None


def female_subjectivity_absent(output: dict[str, Any], include_mixed: bool = False) -> bool:
    visible = {"feminine", "mixed_multiple"} if include_mixed else {"feminine"}
    return all(output[field] not in visible for field in SUBJECTIVITY)


def triple_configuration(output: dict[str, Any], include_mixed: bool = False) -> bool:
    objectified = {"feminine", "mixed_multiple"} if include_mixed else {"feminine"}
    return (
        output["objectification_target_position"] in objectified
        and female_subjectivity_absent(output, include_mixed=include_mixed)
        and "erotic_promotional_approval" in output["editorial_framing_cues"]
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
    return {
        "denominator_n": sum(counts.values()),
        "both_visible_n": counts[(True, True)],
        "first_only_n": first_only,
        "second_only_n": second_only,
        "neither_visible_n": counts[(False, False)],
        "first_minus_second_rate": (
            (first_only - second_only) / sum(counts.values()) if counts else math.nan
        ),
        "exact_p": float(binomtest(first_only, discordant, 0.5).pvalue) if discordant else 1.0,
    }


def exact_contrast(
    rows: list[dict[str, Any]], predicate: Callable[[dict[str, Any]], bool]
) -> dict[str, Any]:
    direct = [row for row in rows if control_group(row["output"]) == "direct_control_without_explicit_nonconsent"]
    force = [row for row in rows if control_group(row["output"]) == "explicit_force_nonconsent"]
    direct_positive = sum(predicate(row["output"]) for row in direct)
    force_positive = sum(predicate(row["output"]) for row in force)
    if direct and force:
        odds, p_value = fisher_exact(
            [
                [direct_positive, len(direct) - direct_positive],
                [force_positive, len(force) - force_positive],
            ]
        )
        risk_difference = direct_positive / len(direct) - force_positive / len(force)
    else:
        odds, p_value, risk_difference = math.nan, math.nan, math.nan
    return {
        "direct_positive_n": direct_positive,
        "direct_denominator_n": len(direct),
        "direct_rate": direct_positive / len(direct) if direct else math.nan,
        "force_positive_n": force_positive,
        "force_denominator_n": len(force),
        "force_rate": force_positive / len(force) if force else math.nan,
        "direct_minus_force_risk_difference": risk_difference,
        "fisher_odds_ratio": float(odds),
        "fisher_two_sided_p": float(p_value),
    }


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
                    int(control_group(row["output"]) == "direct_control_without_explicit_nonconsent")
                    if treatment is None
                    else treatment
                ),
                *[int(row["source_site"] == source) for source in source_levels[1:]],
                *[int(row["length_band"] == length) for length in length_levels[1:]],
            ]
            for row in rows
        ],
        dtype=float,
    )


def firth_control_model(
    rows: list[dict[str, Any]], *, seed: int, bootstrap_reps: int
) -> dict[str, Any]:
    model_rows = [row for row in rows if control_group(row["output"]) is not None]
    source_levels = sorted({row["source_site"] for row in model_rows})
    length_levels = sorted({row["length_band"] for row in model_rows})
    design = control_design(model_rows, source_levels, length_levels)
    outcome = np.asarray(
        [row["output"]["objectification_target_position"] == "feminine" for row in model_rows],
        dtype=float,
    )
    if len(model_rows) < design.shape[1] + 5 or len(set(outcome)) < 2:
        return {
            "analysis_n": len(model_rows),
            "outcome_positive_n": int(np.sum(outcome)),
            "parameters_n": design.shape[1],
            "standardized_force_probability": math.nan,
            "standardized_direct_probability": math.nan,
            "standardized_direct_minus_force_risk_difference": math.nan,
            "bootstrap_risk_difference_95_low": math.nan,
            "bootstrap_risk_difference_95_high": math.nan,
            "bootstrap_reps_successful": 0,
            "bootstrap_failures": bootstrap_reps,
        }
    try:
        fit = fit_firth(design, outcome)
    except RuntimeError:
        fit = fit_firth_lbfgs(design, outcome)
    force_design = control_design(model_rows, source_levels, length_levels, treatment=0)
    direct_design = control_design(model_rows, source_levels, length_levels, treatment=1)
    force_probability = float(np.mean(expit(force_design @ fit["beta"])))
    direct_probability = float(np.mean(expit(direct_design @ fit["beta"])))
    direct_indices = np.asarray(
        [index for index, row in enumerate(model_rows) if control_group(row["output"]) == "direct_control_without_explicit_nonconsent"]
    )
    force_indices = np.asarray(
        [index for index, row in enumerate(model_rows) if control_group(row["output"]) == "explicit_force_nonconsent"]
    )
    rng = np.random.default_rng(seed)
    differences: list[float] = []
    failures = 0
    for _ in range(bootstrap_reps):
        indices = np.concatenate(
            [
                rng.choice(direct_indices, len(direct_indices), replace=True),
                rng.choice(force_indices, len(force_indices), replace=True),
            ]
        )
        boot_rows = [model_rows[int(index)] for index in indices]
        boot_sources = sorted({row["source_site"] for row in boot_rows})
        boot_lengths = sorted({row["length_band"] for row in boot_rows})
        boot_design = control_design(boot_rows, boot_sources, boot_lengths)
        boot_outcome = outcome[indices]
        try:
            try:
                boot_fit = fit_firth(boot_design, boot_outcome)
            except RuntimeError:
                boot_fit = fit_firth_lbfgs(boot_design, boot_outcome)
            boot_force = boot_design.copy()
            boot_direct = boot_design.copy()
            boot_force[:, 1] = 0
            boot_direct[:, 1] = 1
            differences.append(
                float(
                    np.mean(expit(boot_direct @ boot_fit["beta"]))
                    - np.mean(expit(boot_force @ boot_fit["beta"]))
                )
            )
        except RuntimeError:
            failures += 1
    low, high = np.percentile(differences, [2.5, 97.5]) if differences else (math.nan, math.nan)
    return {
        "analysis_n": len(model_rows),
        "outcome_positive_n": int(np.sum(outcome)),
        "parameters_n": design.shape[1],
        "standardized_force_probability": force_probability,
        "standardized_direct_probability": direct_probability,
        "standardized_direct_minus_force_risk_difference": direct_probability - force_probability,
        "bootstrap_risk_difference_95_low": float(low),
        "bootstrap_risk_difference_95_high": float(high),
        "bootstrap_reps_successful": len(differences),
        "bootstrap_failures": failures,
    }


def weighted_ratio(rows: list[dict[str, Any]], predicate: Callable[[dict[str, Any]], bool], weight: str) -> tuple[float, float, float, float]:
    weights = np.asarray([float(row[weight]) for row in rows], dtype=float)
    outcome = np.asarray([predicate(row["output"]) for row in rows], dtype=float)
    estimate = float(np.sum(weights * outcome) / np.sum(weights))
    effective_n = float(np.sum(weights) ** 2 / np.sum(weights**2))
    standard_error = math.sqrt(max(estimate * (1 - estimate), 0.0) / effective_n)
    return estimate, max(0.0, estimate - 1.96 * standard_error), min(1.0, estimate + 1.96 * standard_error), effective_n


def weighted_unresolved_bounds(
    rows: list[dict[str, Any]],
    predicate: Callable[[dict[str, Any]], bool],
    weight: str,
) -> tuple[float, float]:
    """Bound a weighted binary estimate without dropping unresolved records."""
    total_weight = sum(float(row[weight]) for row in rows)
    positive_weight = sum(
        float(row[weight]) * bool(predicate(row["output"]))
        for row in rows
        if row["valid"]
    )
    unresolved_weight = sum(float(row[weight]) for row in rows if not row["valid"])
    return positive_weight / total_weight, (positive_weight + unresolved_weight) / total_weight


def quality_table(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    table = []
    groups = [("all", rows)]
    groups.extend((f"track:{track}", [row for row in rows if row["sampling_track"] == track]) for track in ("probability", "mechanism"))
    groups.extend((f"wave:{wave}", [row for row in rows if row["incremental_wave"] == wave]) for wave in sorted({row["incremental_wave"] for row in rows}))
    for group, subset in groups:
        valid = [row for row in subset if row["valid"]]
        table.append(
            {
                "group": group,
                "records": len(subset),
                "structurally_valid": len(valid),
                "unresolved": len(subset) - len(valid),
                "structural_valid_rate": len(valid) / len(subset) if subset else math.nan,
                "normalized_at_least_once_n": sum(bool(row.get("normalization_actions")) for row in valid),
                "evidence_complete_n": sum(bool((row.get("derived") or {}).get("evidence_complete")) for row in valid),
                "needs_adjudication_n": sum(bool(row["output"].get("needs_adjudication")) for row in valid),
            }
        )
    return table


def control_tables(rows: list[dict[str, Any]], scope: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    contrasts: list[dict[str, Any]] = []
    models: list[dict[str, Any]] = []
    for track in ("probability", "mechanism", "combined_descriptive"):
        subset = rows if track == "combined_descriptive" else [row for row in rows if row["sampling_track"] == track]
        specs = [
            ("feminine_objectification", lambda output: output["objectification_target_position"] == "feminine", False),
            (
                "triple_feminine_objectification_subjectivity_absent_erotic_frame",
                triple_configuration,
                True,
            ),
        ]
        for outcome, predicate, female_target_only in specs:
            opportunity = subset
            if female_target_only:
                opportunity = [row for row in subset if row["output"]["control_target_position"] == "feminine"]
            contrasts.append(
                {
                    "scope": scope,
                    "track": track,
                    "outcome": outcome,
                    **exact_contrast(opportunity, predicate),
                }
            )
        if track != "combined_descriptive":
            models.append(
                {
                    "scope": scope,
                    "track": track,
                    "model": "Firth source+length standardized risk difference",
                    **firth_control_model(
                        subset,
                        seed=2026081600 + (1 if track == "probability" else 2) + len(rows),
                        bootstrap_reps=499 if scope == "discovery" else 999,
                    ),
                }
            )
    return contrasts, models


def privacy_table(rows: list[dict[str, Any]], scope: str) -> list[dict[str, Any]]:
    table: list[dict[str, Any]] = []
    for track in ("probability", "mechanism", "combined_descriptive"):
        subset = rows if track == "combined_descriptive" else [row for row in rows if row["sampling_track"] == track]
        distribution = [row for row in subset if row["output"]["distribution_visibility"] == "visible"]
        creation_distribution = [
            row
            for row in subset
            if row["output"]["material_creation_visibility"] == "visible"
            and row["output"]["distribution_visibility"] == "visible"
        ]
        table.append(
            {
                "scope": scope,
                "track": track,
                "comparison": "distribution_target_visible_vs_actor_visible",
                **paired_counts(
                    distribution,
                    lambda output: position_visible(output["distribution_target_position"]),
                    lambda output: position_visible(output["distribution_actor_position"]),
                ),
            }
        )
        table.append(
            {
                "scope": scope,
                "track": track,
                "comparison": "creation_actor_visible_vs_distribution_actor_visible",
                **paired_counts(
                    creation_distribution,
                    lambda output: position_visible(output["material_creation_actor_position"]),
                    lambda output: position_visible(output["distribution_actor_position"]),
                ),
            }
        )
    return table


def realization_table(rows: list[dict[str, Any]], scope: str) -> list[dict[str, Any]]:
    table = []
    for track in ("probability", "mechanism"):
        subset = [
            row
            for row in rows
            if row["sampling_track"] == track and row["output"]["distribution_visibility"] == "visible"
        ]
        counts = Counter(row["output"]["distribution_actor_realization"] for row in subset)
        for realization, count in sorted(counts.items()):
            table.append(
                {
                    "scope": scope,
                    "track": track,
                    "distribution_actor_realization": realization,
                    "selected_n": count,
                    "denominator_n": len(subset),
                    "proportion": count / len(subset) if subset else math.nan,
                }
            )
    return table or [{"scope": scope, "track": "none", "distribution_actor_realization": "none", "selected_n": 0, "denominator_n": 0, "proportion": math.nan}]


def probability_table(rows: list[dict[str, Any]], scope: str) -> list[dict[str, Any]]:
    probability = [row for row in rows if row["sampling_track"] == "probability"]
    valid_probability = [row for row in probability if row["valid"]]
    specs = [
        ("privacy_chain_relevant", lambda output: output["privacy_chain_relevance"] == "relevant"),
        ("distribution_visible", lambda output: output["distribution_visibility"] == "visible"),
        (
            "creation_and_distribution_visible",
            lambda output: output["material_creation_visibility"] == "visible" and output["distribution_visibility"] == "visible",
        ),
        ("explicit_force_nonconsent_control", lambda output: control_group(output) == "explicit_force_nonconsent"),
        (
            "direct_control_without_explicit_nonconsent",
            lambda output: control_group(output) == "direct_control_without_explicit_nonconsent",
        ),
        ("feminine_objectification", lambda output: output["objectification_target_position"] == "feminine"),
    ]
    table = []
    for metric, predicate in specs:
        for estimand, weight in (("record", "w_record"), ("expression_cluster", "w_cluster")):
            estimate, low, high, effective_n = weighted_ratio(valid_probability, predicate, weight)
            bound_low, bound_high = weighted_unresolved_bounds(probability, predicate, weight)
            table.append(
                {
                    "scope": scope,
                    "metric": metric,
                    "estimand": estimand,
                    "sample_n": len(probability),
                    "valid_n": len(valid_probability),
                    "unresolved_n": len(probability) - len(valid_probability),
                    "weighted_estimate": estimate,
                    "unresolved_as_negative_estimate": bound_low,
                    "unresolved_as_positive_estimate": bound_high,
                    "approximate_95_low": low,
                    "approximate_95_high": high,
                    "kish_effective_n": effective_n,
                    "ci_warning": "descriptive Kish-effective-n approximation; final survey uncertainty requires design-based estimator",
                }
            )
    return table


def robustness_table(rows: list[dict[str, Any]], scope: str) -> list[dict[str, Any]]:
    source_map = {
        source: f"source_{index:02d}"
        for index, source in enumerate(sorted({row["source_site"] for row in rows}), 1)
    }
    table: list[dict[str, Any]] = []
    for track in ("probability", "mechanism"):
        base_rows = [row for row in rows if row["sampling_track"] == track]
        variants: list[tuple[str, str, list[dict[str, Any]]]] = [("main", "all", base_rows)]
        for source, alias in source_map.items():
            variants.append(("leave_one_source_out", alias, [row for row in base_rows if row["source_site"] != source]))
        for length in sorted({row["length_band"] for row in base_rows}):
            variants.append(("leave_one_length_out", length, [row for row in base_rows if row["length_band"] != length]))
        variants.append(
            (
                "exclude_roleplay_bdsm",
                "excluded",
                [row for row in base_rows if "explicit_roleplay_or_bdsm" not in row["output"]["context_frame_codes"]],
            )
        )
        variants.append(
            (
                "exclude_old_objectification_auxiliary",
                "excluded",
                [
                    row
                    for row in base_rows
                    if not (
                        set((row.get("old_v1_output") or {}).get("harm_script_codes", []))
                        & {"humiliation_objectification", "aggressive_sexual_use"}
                    )
                ],
            )
        )
        for variant, removed, subset in variants:
            control = exact_contrast(
                subset,
                lambda output: output["objectification_target_position"] == "feminine",
            )
            distribution = [row for row in subset if row["output"]["distribution_visibility"] == "visible"]
            pair = paired_counts(
                distribution,
                lambda output: position_visible(output["distribution_target_position"]),
                lambda output: position_visible(output["distribution_actor_position"]),
            )
            creation_distribution = [
                row
                for row in subset
                if row["output"]["material_creation_visibility"] == "visible"
                and row["output"]["distribution_visibility"] == "visible"
            ]
            actor_pair = paired_counts(
                creation_distribution,
                lambda output: position_visible(output["material_creation_actor_position"]),
                lambda output: position_visible(output["distribution_actor_position"]),
            )
            table.append(
                {
                    "scope": scope,
                    "track": track,
                    "variant": variant,
                    "removed_level": removed,
                    "analysis_n": len(subset),
                    "control_objectification_risk_difference": control["direct_minus_force_risk_difference"],
                    "control_direct_n": control["direct_denominator_n"],
                    "control_force_n": control["force_denominator_n"],
                    "privacy_pair_n": pair["denominator_n"],
                    "privacy_target_minus_actor_rate": pair["first_minus_second_rate"],
                    "privacy_first_only_n": pair["first_only_n"],
                    "privacy_second_only_n": pair["second_only_n"],
                    "creation_distribution_pair_n": actor_pair["denominator_n"],
                    "creation_minus_distribution_actor_rate": actor_pair[
                        "first_minus_second_rate"
                    ],
                    "creation_actor_first_only_n": actor_pair["first_only_n"],
                    "distribution_actor_second_only_n": actor_pair["second_only_n"],
                }
            )
    return table


def load_rows(scope: str) -> tuple[list[dict[str, Any]], Path]:
    input_path = (
        RUN_DIR / "private/discovery_2000_final_records.jsonl"
        if scope == "discovery"
        else RUN_DIR / "private/validation_5000_final_records.jsonl"
    )
    annotations = {int(row["item_index"]): row for row in read_jsonl(input_path)}
    with SAMPLE.open(encoding="utf-8", newline="") as handle:
        sample = {int(row["item_index"]): row for row in csv.DictReader(handle)}
    old_v1 = {int(row["item_index"]): row for row in read_jsonl(FINAL_V1)}
    if scope == "discovery":
        allowed = {"gate_0500", "discovery_1500"}
    elif scope == "validation":
        allowed = {"validation_3000"}
    else:
        allowed = {"gate_0500", "discovery_1500", "validation_3000"}
    rows = []
    for index, annotation in annotations.items():
        metadata = sample[index]
        if metadata["incremental_wave"] not in allowed:
            continue
        row = {
            **annotation,
            **{key: value for key, value in metadata.items() if key not in {"item_index", "deidentified_title", "record_id", "title_sha256"}},
            "source_site": metadata["source_site"],
            "length_band": metadata["length_band"],
            "sampling_track": metadata["sampling_track"],
            "incremental_wave": metadata["incremental_wave"],
            "old_v1_output": (old_v1.get(int(metadata["old_item_index"])) or {}).get("output"),
        }
        rows.append(row)
    return rows, input_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", choices=("discovery", "validation", "full"), required=True)
    args = parser.parse_args()
    rows, input_path = load_rows(args.scope)
    valid = [row for row in rows if row["valid"]]
    expected = {"discovery": 2000, "validation": 3000, "full": 5000}[args.scope]
    if len(rows) != expected:
        raise RuntimeError(f"{args.scope} has {len(rows)} rows, expected {expected}")
    control, models = control_tables(valid, args.scope)
    outputs = {
        f"quality_summary_{args.scope}.csv": quality_table(rows),
        f"control_contrasts_{args.scope}.csv": control,
        f"control_firth_{args.scope}.csv": models,
        f"privacy_paired_{args.scope}.csv": privacy_table(valid, args.scope),
        f"distribution_actor_realization_{args.scope}.csv": realization_table(valid, args.scope),
        f"probability_estimates_{args.scope}.csv": probability_table(rows, args.scope),
        f"robustness_{args.scope}.csv": robustness_table(valid, args.scope),
    }
    output_manifest = []
    for name, table in outputs.items():
        path = RESULTS / name
        write_csv(path, table)
        output_manifest.append({"path": str(path.relative_to(ROOT)), "rows": len(table), "sha256": sha256(path)})
    manifest = {
        "created_at": utc_now(),
        "status": "complete",
        "scope": args.scope,
        "records": len(rows),
        "structurally_valid": len(valid),
        "unresolved": len(rows) - len(valid),
        "inputs": {
            str(input_path.relative_to(ROOT)): sha256(input_path),
            str(SAMPLE.relative_to(ROOT)): sha256(SAMPLE),
            str(SAMPLE_MANIFEST.relative_to(ROOT)): sha256(SAMPLE_MANIFEST),
            str(FINAL_V1.relative_to(ROOT)): sha256(FINAL_V1),
        },
        "definitions": {
            "explicit_force_nonconsent": "control visible AND (explicit refusal/nonconsent OR overt_force)",
            "direct_control_without_explicit_nonconsent": "control visible, force definition false, AND direct control form visible",
            "female_subjectivity": list(SUBJECTIVITY),
            "probability_ci": "descriptive Kish-effective-n approximation; not final design-based uncertainty",
        },
        "outputs": output_manifest,
        "privacy": "Aggregate outputs exclude titles, evidence spans, identifiers, item indices, and literal source-site names.",
    }
    manifest_path = RESULTS / f"manifest_{args.scope}.json"
    write_json(manifest_path, manifest)
    print(json.dumps({"scope": args.scope, "records": len(rows), "valid": len(valid), "outputs": len(outputs)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
