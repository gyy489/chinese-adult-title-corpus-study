#!/usr/bin/env python3
"""Build manuscript and supplementary tables from public aggregate corpus results.

The script verifies the frozen aggregate inputs recorded in the analysis
manifest and emits only manuscript-level counts and proportions. It never
reads title text, URLs, source names, record identifiers, or evidence spans.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = Path(__file__).resolve()
DEFAULT_ANALYSIS_DIR = (
    ROOT
    / "results/tables/gendered_visibility_paper_corpus_v4_privacy_remediated_analysis_v1"
)
DEFAULT_FREEZE_MANIFEST = (
    ROOT
    / "data/processed/gendered_visibility_paper_corpus_v4_privacy_remediated/manifest.json"
)
DEFAULT_AUDIT_DIR = (
    ROOT / "results/tables/codex_argument_visibility_blind_adjudication_v1"
)
DEFAULT_HUMAN_AUDIT_DIR = (
    ROOT / "data/processed/gendered_visibility_human_audit_400_summary_v2"
)
DEFAULT_HUMAN_RQ1_AUDIT_DIR = (
    ROOT / "data/processed/gendered_visibility_human_audit_rq1_v2"
)
DEFAULT_OUTPUT_DIR = ROOT / "paper/tables"
CORE_ARGUMENT_DECISION = (
    ROOT / "paper/sc_paper_v1/第一阶段核心论证决定_2026-08-19.md"
)
TABLE_SET_ID = "gendered_visibility_manuscript_tables_v1"

PUBLIC_ANALYSIS_LABELS = {
    "B1": "RQ1",
    "C2": "RQ2",
    "C1": "RQ3",
    "C5": "RQ4",
    "C4": "MQ1",
}

PUBLIC_SCOPE_LABELS = {
    "all_frozen_corpus": "Complete analytical sample",
    "probability_tracks_only": "All probability-track texts",
    "run70_probability_only": "Later annotation-sequence probability texts",
    "frozen5000_probability_only": "Frozen-layer probability texts",
    "frozen5000_mechanism_only": "Frozen-layer mechanism-enriched texts",
    "no_adjudication_flag": "Texts without an adjudication flag",
    "evidence_complete": "Texts meeting the evidence-complete diagnostic",
    "no_normalization_action": "Texts without a normalization action",
}

PUBLIC_SENSITIVITY_LABELS = {
    "sampling_composition_scope": "Sampling-composition scopes",
    "quality_scope": "Quality-restricted scopes",
    "leave_one_source_site_out": "Leave-one hashed source-group-out variants",
    "leave_one_length_band_out": "Leave-one length-band-out variants",
}

PUBLIC_BOUNDARY_LABELS = {
    "frozen_v03": "Frozen v0.3 labels",
    "narrow_explicit_victim_or_stealth": "Narrow explicit-victim-or-stealth boundary",
    "codex_broad_leakage_semantics": "Broad leakage-semantics boundary",
}

PUBLIC_DIRECTION_LABELS = {
    "greater_than_0.5": "Descriptive direction: proportion above 50%",
    "greater_than_0": "Descriptive direction: paired difference above zero",
    "descriptive_only": "No directional rule",
}

PUBLIC_POSITION_VALUE_LABELS = {
    "feminine": "feminine",
    "gender_unspecified": "gender unspecified",
    "mixed_multiple": "mixed multiple",
    "not_visible": "not visible",
    "masculine": "masculine",
    "gender_diverse": "gender diverse",
    "mutual_or_reciprocal": "mutual or reciprocal",
    "unclear": "unclear",
}

PUBLIC_AUTHORIZATION_CATEGORY_LABELS = {
    "no_visible_authorization_language": "No visible authorization language",
    "unauthorized_explicit": "Explicitly nonauthorized wording",
    "authorized_explicit": "Explicitly authorized wording",
    "unclear": "Unclear authorization wording",
    "mixed_or_stage_conflict": "Mixed or stage-conflicting authorization wording",
}

PUBLIC_FIELD_LABELS = {
    "record_validity": "Record validity",
    "privacy_chain_relevance": "Privacy-chain relevance",
    "material_creation_visibility": "Material-creation visibility",
    "material_creation_target_position": "Material-creation target position",
    "distribution_visibility": "Distribution visibility",
    "distribution_actor_position": "Distribution actor position",
    "distribution_actor_realization": "Distribution actor realization",
    "distribution_target_position": "Distribution target position",
    "distribution_authorization_visibility": (
        "Distribution authorization-language visibility"
    ),
    "exposure_visibility": "Exposure visibility",
    "exposure_target_position": "Exposure target position",
    "objectification_target_position": "Objectification target position",
    "desire_holder_position": "Desire-holder position",
    "pleasure_holder_position": "Pleasure-holder position",
    "refusal_resistance_position": "Refusal/resistance position",
    "attributed_speech_position": "Attributed-speech position",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise TypeError(f"Expected a JSON object: {path}")
    return payload


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def one_row(rows: list[dict[str, str]], **selectors: str) -> dict[str, str]:
    matches = [
        row
        for row in rows
        if all(row.get(field) == value for field, value in selectors.items())
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Expected one aggregate row for {selectors}; got {len(matches)}"
        )
    return matches[0]


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        raise ValueError(f"Refusing to write an empty table: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(rows[0]), lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def markdown_table(
    rows: list[dict[str, object]], columns: tuple[tuple[str, str], ...]
) -> str:
    def cell(value: object) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")

    header = "| " + " | ".join(label for _, label in columns) + " |"
    divider = "| " + " | ".join("---" for _ in columns) + " |"
    body = [
        "| " + " | ".join(cell(row[key]) for key, _ in columns) + " |" for row in rows
    ]
    return "\n".join((header, divider, *body))


def percent(value: str | float) -> str:
    return f"{float(value) * 100:.2f}"


def wilson_interval(
    selected: int, denominator: int, z: float = 1.959963984540054
) -> tuple[float, float]:
    if denominator <= 0:
        raise ValueError("Wilson interval requires a positive denominator")
    proportion = selected / denominator
    z2 = z * z
    denominator_term = 1 + z2 / denominator
    center = (proportion + z2 / (2 * denominator)) / denominator_term
    half_width = (
        z
        * math.sqrt(
            proportion * (1 - proportion) / denominator
            + z2 / (4 * denominator * denominator)
        )
        / denominator_term
    )
    return center - half_width, center + half_width


def verify_aggregate(path: Path, analysis_manifest: dict[str, Any]) -> str:
    actual = sha256_file(path)
    recorded = analysis_manifest.get("outputs", {}).get(relative(path))
    if recorded != actual:
        raise ValueError(f"Aggregate input checksum mismatch: {relative(path)}")
    return actual


def verify_named_output(path: Path, output_manifest: dict[str, Any]) -> str:
    """Verify an aggregate whose manifest records output basenames."""
    actual = sha256_file(path)
    recorded = output_manifest.get("outputs", {}).get(path.name)
    if recorded != actual:
        raise ValueError(f"Named aggregate checksum mismatch: {relative(path)}")
    return actual


def build_tables(
    analysis_dir: Path = DEFAULT_ANALYSIS_DIR,
    freeze_manifest_path: Path = DEFAULT_FREEZE_MANIFEST,
) -> tuple[dict[str, list[dict[str, object]]], dict[str, str]]:
    analysis_manifest_path = analysis_dir / "manifest.json"
    candidate_path = analysis_dir / "candidate_metrics.csv"
    descriptives_path = analysis_dir / "corpus_descriptives.csv"
    positions_path = analysis_dir / "position_distributions.csv"
    authorization_path = analysis_dir / "authorization_boundary_sensitivity.csv"
    sensitivity_path = analysis_dir / "sensitivity_ranges.csv"
    sampling_path = analysis_dir / "sampling_design_sensitivity.csv"
    quality_path = analysis_dir / "quality_sensitivity.csv"
    audit_manifest_path = DEFAULT_AUDIT_DIR / "manifest.json"
    field_agreement_path = DEFAULT_AUDIT_DIR / "field_agreement.csv"
    human_audit_manifest_path = DEFAULT_HUMAN_AUDIT_DIR / "manifest.json"
    human_field_agreement_path = DEFAULT_HUMAN_AUDIT_DIR / "field_agreement.csv"
    human_pair_summary_path = DEFAULT_HUMAN_AUDIT_DIR / "pair_summary.csv"
    human_rq1_manifest_path = DEFAULT_HUMAN_RQ1_AUDIT_DIR / "manifest.json"
    human_rq1_estimates_path = DEFAULT_HUMAN_RQ1_AUDIT_DIR / "rq1_estimates.csv"
    human_rq1_cells_path = DEFAULT_HUMAN_RQ1_AUDIT_DIR / "rq1_paired_cells.csv"
    human_rq1_agreement_path = (
        DEFAULT_HUMAN_RQ1_AUDIT_DIR / "rq1_pairwise_agreement.csv"
    )
    human_rq1_confusion_path = (
        DEFAULT_HUMAN_RQ1_AUDIT_DIR / "rq1_pairwise_confusion.csv"
    )
    analysis_manifest = load_json(analysis_manifest_path)
    audit_manifest = load_json(audit_manifest_path)
    human_audit_manifest = load_json(human_audit_manifest_path)
    human_rq1_manifest = load_json(human_rq1_manifest_path)
    freeze_manifest = load_json(freeze_manifest_path)
    input_hashes = {
        relative(analysis_manifest_path): sha256_file(analysis_manifest_path)
    }
    for path in (
        candidate_path,
        descriptives_path,
        positions_path,
        authorization_path,
        sensitivity_path,
        sampling_path,
        quality_path,
    ):
        input_hashes[relative(path)] = verify_aggregate(path, analysis_manifest)

    input_hashes[relative(audit_manifest_path)] = sha256_file(audit_manifest_path)
    field_agreement_hash = sha256_file(field_agreement_path)
    if (
        audit_manifest.get("outputs", {}).get(relative(field_agreement_path))
        != field_agreement_hash
    ):
        raise ValueError("Cross-model field-agreement checksum mismatch")
    input_hashes[relative(field_agreement_path)] = field_agreement_hash

    if human_audit_manifest.get("analysis_id") != (
        "gendered_visibility_human_audit_400_summary_v2"
    ) or human_audit_manifest.get("status") != (
        "complete_frozen_aggregate_correction"
    ) or (
        human_audit_manifest.get("counts", {}).get("sample_records") != 400
    ) or (
        human_audit_manifest.get("counts", {}).get(
            "researcher_pilot_records_included"
        )
        != 0
    ):
        raise ValueError("Human-audit aggregate is not the current 400-record summary")
    input_hashes[relative(human_audit_manifest_path)] = sha256_file(
        human_audit_manifest_path
    )
    input_hashes[relative(human_field_agreement_path)] = verify_named_output(
        human_field_agreement_path, human_audit_manifest
    )
    input_hashes[relative(human_pair_summary_path)] = verify_named_output(
        human_pair_summary_path, human_audit_manifest
    )

    if (
        human_rq1_manifest.get("analysis_id")
        != "gendered_visibility_human_audit_rq1_v2"
        or human_rq1_manifest.get("status")
        != "complete_frozen_aggregate_correction"
        or human_rq1_manifest.get("counts", {}).get("sample_records") != 400
        or human_rq1_manifest.get("counts", {}).get(
            "researcher_pilot_records_included"
        )
        != 0
    ):
        raise ValueError("Human RQ1 audit aggregate is not complete and frozen")
    input_hashes[relative(human_rq1_manifest_path)] = sha256_file(
        human_rq1_manifest_path
    )
    for path in (
        human_rq1_estimates_path,
        human_rq1_cells_path,
        human_rq1_agreement_path,
        human_rq1_confusion_path,
    ):
        input_hashes[relative(path)] = verify_named_output(path, human_rq1_manifest)

    freeze_hash = sha256_file(freeze_manifest_path)
    if (
        analysis_manifest.get("inputs", {}).get(relative(freeze_manifest_path))
        != freeze_hash
    ):
        raise ValueError("Frozen corpus manifest checksum mismatch")
    input_hashes[relative(freeze_manifest_path)] = freeze_hash

    core_argument_text = CORE_ARGUMENT_DECISION.read_text(encoding="utf-8")
    if "唯一中心结果" not in core_argument_text or "第二层结果" not in core_argument_text:
        raise ValueError("Core-argument evidence hierarchy is not frozen")
    input_hashes[relative(CORE_ARGUMENT_DECISION)] = sha256_file(
        CORE_ARGUMENT_DECISION
    )

    candidates = load_csv(candidate_path)
    descriptives = load_csv(descriptives_path)
    positions = load_csv(positions_path)
    authorization = load_csv(authorization_path)
    sensitivities = load_csv(sensitivity_path)
    sampling_scopes = load_csv(sampling_path)
    quality_scopes = load_csv(quality_path)
    field_agreement = load_csv(field_agreement_path)
    human_field_agreement = load_csv(human_field_agreement_path)
    human_pair_summary = load_csv(human_pair_summary_path)
    human_rq1_estimates = load_csv(human_rq1_estimates_path)
    human_rq1_cells = load_csv(human_rq1_cells_path)
    human_rq1_agreement = load_csv(human_rq1_agreement_path)
    human_rq1_confusion = load_csv(human_rq1_confusion_path)
    counts = freeze_manifest["counts"]
    scope = freeze_manifest["scope"]

    corpus_n = int(one_row(descriptives, statistic="paper_corpus_records")["count"])
    if int(scope["paper_analytical_corpus_unique_final_texts"]) != corpus_n:
        raise ValueError("Frozen corpus count drift")

    duplicate_contributions = int(counts["v1_extra_records_beyond_first"]) + int(
        counts.get("v4_extra_contributions_removed", 0)
    )

    formation = [
        {
            "stage_or_component": "Unique-text candidate frame",
            "included_n": int(scope["source_frame_unique_final_texts"]),
            "excluded_n": "",
            "status_or_rule": "Not the inferential population",
        },
        {
            "stage_or_component": "Valid v0.3 record-level AI outputs",
            "included_n": int(counts["v1_valid_record_members"]),
            "excluded_n": "",
            "status_or_rule": "Outputs retained after validity screening",
        },
        {
            "stage_or_component": "Repeated-text record contributions excluded",
            "included_n": "",
            "excluded_n": duplicate_contributions,
            "status_or_rule": (
                "One label-independent representative per final-text hash, "
                "reapplied after privacy remediation"
            ),
        },
        {
            "stage_or_component": "Unique-text analytical sample",
            "included_n": corpus_n,
            "excluded_n": int(scope["unique_final_texts_outside_paper_corpus"]),
            "status_or_rule": "Each final-text hash contributes once",
        },
        {
            "stage_or_component": "Probability-track texts",
            "included_n": int(
                one_row(descriptives, statistic="probability_track_records")["count"]
            ),
            "excluded_n": "",
            "status_or_rule": "Sampling-composition component; not a population weight",
        },
        {
            "stage_or_component": "Mechanism-enriched texts",
            "included_n": int(
                one_row(descriptives, statistic="mechanism_track_records")["count"]
            ),
            "excluded_n": "",
            "status_or_rule": "Enriched component",
        },
    ]

    target = one_row(
        candidates, candidate="B1", metric="distribution_target_position_visible"
    )
    actor = one_row(
        candidates, candidate="B1", metric="distribution_actor_position_visible"
    )
    paired = one_row(
        candidates,
        candidate="B1",
        metric="target_minus_actor_visibility_paired_difference",
    )
    denominator = int(paired["denominator_n"])
    visibility = [
        {
            "measure": "Distribution target position visible",
            "selected_n": int(target["selected_n"]),
            "denominator_n": denominator,
            "percent": percent(target["estimate"]),
            "paired_cell_or_note": "",
        },
        {
            "measure": "Distribution actor position visible",
            "selected_n": int(actor["selected_n"]),
            "denominator_n": denominator,
            "percent": percent(actor["estimate"]),
            "paired_cell_or_note": "",
        },
        {
            "measure": "Target visible; actor outside contract-defined visible set",
            "selected_n": int(paired["selected_n"]),
            "denominator_n": denominator,
            "percent": percent(int(paired["selected_n"]) / denominator),
            "paired_cell_or_note": "Discordant cell",
        },
        {
            "measure": "Actor visible; target outside contract-defined visible set",
            "selected_n": int(paired["comparison_selected_n"]),
            "denominator_n": denominator,
            "percent": percent(int(paired["comparison_selected_n"]) / denominator),
            "paired_cell_or_note": "Discordant cell",
        },
        {
            "measure": "Both positions visible",
            "selected_n": int(paired["both_n"]),
            "denominator_n": denominator,
            "percent": percent(int(paired["both_n"]) / denominator),
            "paired_cell_or_note": "Concordant cell",
        },
        {
            "measure": "Neither position in contract-defined visible set",
            "selected_n": int(paired["neither_n"]),
            "denominator_n": denominator,
            "percent": percent(int(paired["neither_n"]) / denominator),
            "paired_cell_or_note": "Concordant cell",
        },
        {
            "measure": "Target minus actor visibility",
            "selected_n": "",
            "denominator_n": denominator,
            "percent": percent(paired["estimate"]),
            "paired_cell_or_note": "Percentage-point paired difference",
        },
    ]
    if sum(int(row["selected_n"]) for row in visibility[2:6]) != denominator:
        raise ValueError("B1 paired cells do not reconcile")

    transition_specs = (
        (
            "Creation to distribution",
            "creation_to_distribution_target_position_consistency",
        ),
        (
            "Distribution to exposure",
            "distribution_to_exposure_target_position_consistency",
        ),
        (
            "Creation, distribution, and exposure",
            "three_stage_target_position_consistency",
        ),
    )
    consistency: list[dict[str, object]] = []
    for label, metric in transition_specs:
        row = one_row(candidates, candidate="C2", metric=metric)
        consistency.append(
            {
                "visible_stage_comparison": label,
                "exactly_consistent_n": int(row["selected_n"]),
                "eligible_n": int(row["denominator_n"]),
                "percent": percent(row["estimate"]),
                "definition": "Exact equality of coded target-position labels",
            }
        )

    supporting: list[dict[str, object]] = []
    distribution_composition = [
        row
        for row in positions
        if row["context"] == "distribution_visible"
        and row["field"] == "distribution_target_position"
    ]
    if sum(int(row["count"]) for row in distribution_composition) != denominator:
        raise ValueError("Distribution-target composition does not reconcile")
    for row in distribution_composition:
        supporting.append(
            {
                "analysis": PUBLIC_ANALYSIS_LABELS["C1"],
                "measure_or_boundary": (
                    f"Distribution target: {PUBLIC_POSITION_VALUE_LABELS[row['value']]}"
                ),
                "selected_n": int(row["count"]),
                "comparison_n": "",
                "eligible_n": int(row["denominator_n"]),
                "percent": percent(row["proportion"]),
                "manuscript_role": "Complete mutually exclusive target composition",
            }
        )

    support_specs = (
        (
            "C1",
            "Feminine or mixed-multiple distribution target",
            "feminine_or_mixed_distribution_target",
        ),
        ("C5", "Feminine objectification visible", "feminine_objectification"),
        (
            "C5",
            "Feminine coding in any of four person-position fields",
            "any_feminine_person_position",
        ),
        (
            "C5",
            (
                "Feminine objectification; no feminine coding in any of four "
                "person-position fields"
            ),
            "objectification_and_all_four_person_positions_absent",
        ),
    )
    for candidate, label, metric in support_specs:
        row = one_row(candidates, candidate=candidate, metric=metric)
        supporting.append(
            {
                "analysis": PUBLIC_ANALYSIS_LABELS[candidate],
                "measure_or_boundary": label,
                "selected_n": int(row["selected_n"]),
                "comparison_n": (
                    int(row["comparison_selected_n"])
                    if row["comparison_selected_n"]
                    else ""
                ),
                "eligible_n": int(row["denominator_n"]),
                "percent": percent(row["estimate"]),
                "manuscript_role": (
                    "Exploratory instrument-dependent result"
                    if candidate == "C5"
                    else "Supporting descriptive result"
                ),
            }
        )

    person_position_fields = (
        ("desire_holder_position", "Feminine desire-holder position visible"),
        ("pleasure_holder_position", "Feminine pleasure-holder position visible"),
        (
            "refusal_resistance_position",
            "Feminine refusal/resistance position visible",
        ),
        ("attributed_speech_position", "Feminine attributed-speech position visible"),
    )
    for field, label in person_position_fields:
        row = one_row(
            positions,
            context="strict_feminine_distribution",
            field=field,
            value="feminine",
        )
        supporting.append(
            {
                "analysis": PUBLIC_ANALYSIS_LABELS["C5"],
                "measure_or_boundary": label,
                "selected_n": int(row["count"]),
                "comparison_n": "",
                "eligible_n": int(row["denominator_n"]),
                "percent": percent(row["proportion"]),
                "manuscript_role": (
                    "Exploratory non-exhaustive person-position cue"
                ),
            }
        )
    for boundary in authorization:
        supporting.append(
            {
                "analysis": PUBLIC_ANALYSIS_LABELS["C4"],
                "measure_or_boundary": (
                    "No visible authorization language: "
                    f"{PUBLIC_BOUNDARY_LABELS[boundary['boundary']]}"
                ),
                "selected_n": int(boundary["no_visible_authorization_n"]),
                "comparison_n": "",
                "eligible_n": int(boundary["distribution_opportunity_n"]),
                "percent": percent(boundary["no_visible_authorization_rate"]),
                "manuscript_role": "Definition sensitivity only",
            }
        )

    directional_failures = sum(
        int(row["direction_failure_count"])
        for row in sensitivities
        if row["candidate"] in {"B1", "C1", "C2", "C5"}
        and row["direction_evaluated_variants"] != "0"
    )
    if directional_failures != 0:
        raise ValueError("A pre-specified directional sensitivity check failed")

    evidence_roles = {
        "B1": "Primary result",
        "C2": "Secondary within-instrument diagnostic",
        "C1": "Supporting result",
        "C5": "Exploratory instrument-dependent result",
        "C4": "Measurement sensitivity only",
    }
    metric_labels = {
        "strict_feminine_distribution_target": (
            "Strictly feminine distribution target"
        ),
        "feminine_or_mixed_distribution_target": (
            "Feminine or mixed-multiple distribution target"
        ),
        "distribution_target_position_visible": (
            "Distribution target position visible"
        ),
        "distribution_actor_position_visible": ("Distribution actor position visible"),
        "target_minus_actor_visibility_paired_difference": (
            "Target-position minus actor-position visibility"
        ),
        "creation_to_distribution_target_position_consistency": (
            "Creation-to-distribution target-label consistency"
        ),
        "distribution_to_exposure_target_position_consistency": (
            "Distribution-to-exposure target-label consistency"
        ),
        "three_stage_target_position_consistency": (
            "Three-stage target-label consistency"
        ),
        "no_visible_authorization_language_frozen_v03": (
            "No visible authorization language under frozen v0.3 labels"
        ),
        "feminine_objectification": "Feminine objectification visible",
        "any_feminine_person_position": (
            "Feminine coding in any of four person-position fields"
        ),
        "objectification_and_all_four_person_positions_absent": (
            "Feminine objectification with no feminine coding in four "
            "person-position fields"
        ),
        "objectification_minus_any_person_position_paired_difference": (
            "Feminine objectification minus feminine coding in any of four "
            "person-position fields"
        ),
    }
    heuristic_warning = (
        "Conventional unique-title-text heuristic only; no external "
        "superpopulation; not sampling error for the retained analytical sample; "
        "approximate independence of unique title texts"
    )
    heuristic_proportions: list[dict[str, object]] = []

    def add_proportion(
        analysis: str,
        label: str,
        role: str,
        selected: int,
        eligible: int,
        opportunity: str,
        low: str | float | None = None,
        high: str | float | None = None,
    ) -> None:
        interval_low, interval_high = (
            (float(low), float(high))
            if low is not None and high is not None
            else wilson_interval(selected, eligible)
        )
        heuristic_proportions.append(
            {
                "analysis": analysis,
                "metric_public_label": label,
                "opportunity_definition": opportunity,
                "manuscript_role": role,
                "numerator_n": selected,
                "denominator_n": eligible,
                "percent": percent(selected / eligible),
                "wilson_95_low_percent": percent(interval_low),
                "wilson_95_high_percent": percent(interval_high),
                "unit": "unique title text",
                "heuristic_warning": heuristic_warning,
            }
        )

    position_labels = {
        value: f"Distribution target: {label}"
        for value, label in PUBLIC_POSITION_VALUE_LABELS.items()
    }
    for row in distribution_composition:
        add_proportion(
            PUBLIC_ANALYSIS_LABELS["C1"],
            position_labels[row["value"]],
            "Supporting result",
            int(row["count"]),
            int(row["denominator_n"]),
            "Visible distribution event",
        )

    candidate_proportion_specs = (
        ("C1", "feminine_or_mixed_distribution_target", "Visible distribution event"),
        ("B1", "distribution_target_position_visible", "Visible distribution event"),
        ("B1", "distribution_actor_position_visible", "Visible distribution event"),
        (
            "C2",
            "creation_to_distribution_target_position_consistency",
            "Creation and distribution stages and target positions visible",
        ),
        (
            "C2",
            "distribution_to_exposure_target_position_consistency",
            "Distribution and exposure stages and target positions visible",
        ),
        (
            "C2",
            "three_stage_target_position_consistency",
            "Creation, distribution, and exposure stages and target positions visible",
        ),
        ("C5", "feminine_objectification", "Strict-feminine distribution target"),
        ("C5", "any_feminine_person_position", "Strict-feminine distribution target"),
        (
            "C5",
            "objectification_and_all_four_person_positions_absent",
            "Strict-feminine distribution target",
        ),
    )
    for analysis, metric, opportunity in candidate_proportion_specs:
        row = one_row(candidates, candidate=analysis, metric=metric)
        add_proportion(
            PUBLIC_ANALYSIS_LABELS[analysis],
            metric_labels.get(metric, metric),
            evidence_roles[analysis],
            int(row["selected_n"]),
            int(row["denominator_n"]),
            opportunity,
            row["ci95_low"],
            row["ci95_high"],
        )

    for boundary in authorization:
        add_proportion(
            PUBLIC_ANALYSIS_LABELS["C4"],
            "No visible authorization language: "
            f"{PUBLIC_BOUNDARY_LABELS[boundary['boundary']]}",
            "Measurement sensitivity only",
            int(boundary["no_visible_authorization_n"]),
            int(boundary["distribution_opportunity_n"]),
            "Visible distribution event",
            boundary["no_visible_wilson_95_low"],
            boundary["no_visible_wilson_95_high"],
        )

    for field, label in person_position_fields:
        row = one_row(
            positions,
            context="strict_feminine_distribution",
            field=field,
            value="feminine",
        )
        add_proportion(
            PUBLIC_ANALYSIS_LABELS["C5"],
            label,
            "Supporting result component",
            int(row["count"]),
            int(row["denominator_n"]),
            "Strict-feminine distribution target",
        )

    paired_heuristics: list[dict[str, object]] = []
    for analysis, metric, label, a_label, b_label in (
        (
            "B1",
            "target_minus_actor_visibility_paired_difference",
            "Target-position visibility minus actor-position visibility",
            "Target visible",
            "Actor visible",
        ),
        (
            "C5",
            "objectification_minus_any_person_position_paired_difference",
            (
                "Feminine objectification minus feminine coding in any of four "
                "person-position fields"
            ),
            "Feminine objectification",
            "Feminine coding in any of four person-position fields",
        ),
    ):
        row = one_row(candidates, candidate=analysis, metric=metric)
        a_only = int(row["selected_n"])
        b_only = int(row["comparison_selected_n"])
        both = int(row["both_n"])
        neither = int(row["neither_n"])
        eligible = int(row["denominator_n"])
        paired_heuristics.append(
            {
                "analysis": PUBLIC_ANALYSIS_LABELS[analysis],
                "source_analysis_id": analysis,
                "contrast": label,
                "a_label": a_label,
                "b_label": b_label,
                "a_only_n": a_only,
                "b_only_n": b_only,
                "both_n": both,
                "neither_n": neither,
                "eligible_n": eligible,
                "a_rate_percent": percent((a_only + both) / eligible),
                "b_rate_percent": percent((b_only + both) / eligible),
                "paired_difference_pp": percent(row["estimate"]),
                "paired_normal_95_low_pp": percent(row["ci95_low"]),
                "paired_normal_95_high_pp": percent(row["ci95_high"]),
                "heuristic_warning": heuristic_warning,
            }
        )

    sensitivity_ranges = [
        {
            "sensitivity_family": PUBLIC_SENSITIVITY_LABELS[row["sensitivity_family"]],
            "source_sensitivity_family_id": row["sensitivity_family"],
            "analysis": PUBLIC_ANALYSIS_LABELS[row["candidate"]],
            "source_analysis_id": row["candidate"],
            "source_metric_id": row["metric"],
            "measure": metric_labels.get(row["metric"], row["metric"]),
            "variant_count": int(row["variant_count"]),
            "minimum_eligible_n": int(row["minimum_denominator_n"]),
            "maximum_eligible_n": int(row["maximum_denominator_n"]),
            "minimum_percent": percent(row["minimum_estimate"]),
            "maximum_percent": percent(row["maximum_estimate"]),
            "direction_evaluated_variants": int(row["direction_evaluated_variants"]),
            "direction_failure_count": (
                int(row["direction_failure_count"])
                if int(row["direction_evaluated_variants"]) > 0
                else "not_applicable"
            ),
        }
        for row in sensitivities
    ]

    def scope_rows(rows: list[dict[str, str]]) -> list[dict[str, object]]:
        return [
            {
                "scope": PUBLIC_SCOPE_LABELS[row["scope"]],
                "source_scope_id": row["scope"],
                "scope_records": int(row["scope_records"]),
                "analysis": PUBLIC_ANALYSIS_LABELS[row["candidate"]],
                "source_analysis_id": row["candidate"],
                "source_metric_id": row["metric"],
                "measure": metric_labels.get(row["metric"], row["metric"]),
                "selected_n": int(row["selected_n"]),
                "comparison_selected_n": (
                    int(row["comparison_selected_n"])
                    if row["comparison_selected_n"]
                    else ""
                ),
                "eligible_n": int(row["denominator_n"]),
                "estimate_percent": percent(row["estimate"]),
                "heuristic_95_low_percent": percent(row["ci95_low"]),
                "heuristic_95_high_percent": percent(row["ci95_high"]),
                "direction_rule": PUBLIC_DIRECTION_LABELS[row["direction_rule"]],
                "direction_retained": {
                    "True": "Yes",
                    "not_applicable": "Not evaluated",
                }[row["direction_retained"]],
            }
            for row in rows
        ]

    authorization_composition: list[dict[str, object]] = []
    for boundary in authorization:
        opportunity_n = int(boundary["distribution_opportunity_n"])
        category_counts = {
            "no_visible_authorization_language": int(
                boundary["no_visible_authorization_n"]
            ),
            "unauthorized_explicit": int(boundary["unauthorized_explicit_n"]),
            "authorized_explicit": int(boundary["authorized_explicit_n"]),
            "unclear": int(boundary["unclear_n"]),
        }
        category_counts["mixed_or_stage_conflict"] = opportunity_n - sum(
            category_counts.values()
        )
        for category, category_n in category_counts.items():
            authorization_composition.append(
                {
                    "boundary": PUBLIC_BOUNDARY_LABELS[boundary["boundary"]],
                    "source_boundary_id": boundary["boundary"],
                    "category": PUBLIC_AUTHORIZATION_CATEGORY_LABELS[category],
                    "source_category_id": category,
                    "category_n": category_n,
                    "opportunity_n": opportunity_n,
                    "percent": percent(category_n / opportunity_n),
                    "interpretation": (
                        "Deterministic lexical sensitivity; not actual authorization or consent"
                    ),
                }
            )

    cross_model_agreement = [
        {
            "field": PUBLIC_FIELD_LABELS[row["field"]],
            "source_field_id": row["field"],
            "records": int(row["records"]),
            "exact_agreement_n": int(row["exact_agreement_n"]),
            "exact_agreement_percent": percent(row["exact_agreement_rate"]),
            "cohen_kappa": row["cohen_kappa"],
            "reference_warning": (
                "One blinded cross-model coding pass; not human coding, a gold "
                "standard, or an accuracy estimate"
            ),
        }
        for row in field_agreement
        if row["model"] == "deepseek_frozen_v03"
    ]
    if len(cross_model_agreement) != 16:
        raise ValueError("Expected 16 frozen-v0.3 cross-model field rows")

    human_pair_labels = {
        "ai_vs_reviewer_1": "Frozen AI vs human coder 1",
        "ai_vs_reviewer_2": "Frozen AI vs human coder 2",
        "reviewer_1_vs_reviewer_2": "Human coder 1 vs human coder 2",
    }
    human_instruction_context = {
        "ai_vs_reviewer_1": "Independent first-exposure, AI-blind human coding",
        "ai_vs_reviewer_2": "Independent first-exposure, AI-blind human coding",
        "reviewer_1_vs_reviewer_2": (
            "Two independent first-exposure, AI-blind human coders"
        ),
    }
    human_audit_field_rows = [
        {
            "comparison": human_pair_labels[row["pair"]],
            "field": PUBLIC_FIELD_LABELS[row["field"]],
            "source_field_id": row["field"],
            "records": int(row["records"]),
            "exact_agreement_n": int(row["exact_agreement_n"]),
            "exact_agreement_percent": f"{float(row['exact_agreement_percent']):.2f}",
            "cohen_kappa": row["cohen_kappa"],
            "kappa_status": row["kappa_status"],
            "design_weighted_exact_agreement_percent": (
                f"{float(row['design_weighted_exact_agreement_percent']):.2f}"
            ),
            "instruction_context": human_instruction_context[row["pair"]],
            "interpretation": (
                "Validation of AI coding accuracy against independent human "
                "reference judgments; no overwrite of frozen labels"
            ),
        }
        for row in human_field_agreement
    ]
    if len(human_audit_field_rows) != 48:
        raise ValueError("Expected 48 pair-by-field human-audit rows")

    human_audit_pair_rows = [
        {
            "comparison": human_pair_labels[row["pair"]],
            "fields": int(row["fields"]),
            "records": int(row["records"]),
            "decisions": int(row["decisions"]),
            "exact_agreement_n": int(row["exact_agreement_n"]),
            "micro_exact_agreement_percent": (
                f"{float(row['micro_exact_agreement_percent']):.2f}"
            ),
            "design_weighted_micro_exact_agreement_percent": (
                f"{float(row['design_weighted_micro_exact_agreement_percent']):.2f}"
            ),
            "micro_kappa": "not computed across distinct field domains",
            "instruction_context": human_instruction_context[row["pair"]],
        }
        for row in human_pair_summary
    ]
    if len(human_audit_pair_rows) != 3:
        raise ValueError("Expected three human-audit pair-summary rows")

    rq1_scope_labels = {
        "source_specific_eligibility": "Source-specific eligibility",
        "ai_reference_eligible": "AI-reference eligibility",
        "all_three_eligible": "All-three common eligibility",
        "full_sample": "Full 400-record sample",
    }
    rq1_source_labels = {
        "frozen_ai_v0_3": "Frozen AI v0.3",
        "reviewer_1": "Human coder 1",
        "reviewer_2": "Human coder 2",
    }
    rq1_pair_labels = {
        "ai_vs_reviewer_1": "Frozen AI vs human coder 1",
        "ai_vs_reviewer_2": "Frozen AI vs human coder 2",
        "reviewer_1_vs_reviewer_2": "Human coder 1 vs human coder 2",
    }
    rq1_measure_labels = {
        "eligibility_binary": "RQ1 eligibility (binary)",
        "target_visibility_binary": "Distribution-target visibility (binary)",
        "actor_visibility_binary": "Distribution-actor visibility (binary)",
        "target_position_exact": "Distribution-target position (exact category)",
        "actor_position_exact": "Distribution-actor position (exact category)",
    }
    rq1_cell_labels = {
        "target_visible_actor_outside": "Target visible; actor outside visible set",
        "target_outside_actor_visible": "Target outside visible set; actor visible",
        "both_visible": "Both positions visible",
        "neither_visible": "Neither position visible",
    }

    human_rq1_estimate_rows = [
        {
            "scope": rq1_scope_labels[row["scope_id"]],
            "source": rq1_source_labels[row["source_id"]],
            "eligible_n": int(row["unweighted_scope_n"]),
            "target_visible_n": int(row["unweighted_target_visible_n"]),
            "unweighted_target_visible_percent": (
                f"{float(row['unweighted_target_visible_percent']):.2f}"
            ),
            "actor_visible_n": int(row["unweighted_actor_visible_n"]),
            "unweighted_actor_visible_percent": (
                f"{float(row['unweighted_actor_visible_percent']):.2f}"
            ),
            "unweighted_target_minus_actor_pp": (
                f"{float(row['unweighted_target_minus_actor_pp']):.2f}"
            ),
            "design_weighted_target_visible_percent": (
                f"{float(row['design_weighted_target_visible_percent']):.2f}"
            ),
            "design_weighted_actor_visible_percent": (
                f"{float(row['design_weighted_actor_visible_percent']):.2f}"
            ),
            "design_weighted_target_minus_actor_pp": (
                f"{float(row['design_weighted_target_minus_actor_pp']):.2f}"
            ),
            "design_weighted_difference_ci95_low_pp": (
                f"{float(row['design_weighted_target_minus_actor_ci95_low_pp']):.2f}"
            ),
            "design_weighted_difference_ci95_high_pp": (
                f"{float(row['design_weighted_target_minus_actor_ci95_high_pp']):.2f}"
            ),
            "interval_method": row["interval_method"],
        }
        for row in human_rq1_estimates
    ]
    if len(human_rq1_estimate_rows) != 9:
        raise ValueError("Expected nine source-by-scope RQ1 audit estimates")

    human_rq1_cell_rows = [
        {
            "scope": rq1_scope_labels[row["scope_id"]],
            "source": rq1_source_labels[row["source_id"]],
            "paired_cell": rq1_cell_labels[row["cell_id"]],
            "eligible_n": int(row["unweighted_scope_n"]),
            "cell_n": int(row["unweighted_cell_n"]),
            "unweighted_cell_percent": (
                f"{float(row['unweighted_cell_percent']):.2f}"
            ),
            "design_weighted_cell_percent": (
                f"{float(row['design_weighted_cell_percent']):.2f}"
            ),
        }
        for row in human_rq1_cells
    ]
    if len(human_rq1_cell_rows) != 36:
        raise ValueError("Expected 36 RQ1 audit paired-cell rows")

    human_rq1_agreement_rows = [
        {
            "scope": rq1_scope_labels[row["scope_id"]],
            "measure": rq1_measure_labels[row["measure_id"]],
            "comparison": rq1_pair_labels[row["pair_id"]],
            "records": int(row["records_n"]),
            "exact_agreement_n": int(row["exact_agreement_n"]),
            "exact_agreement_percent": (
                f"{float(row['exact_agreement_percent']):.2f}"
            ),
            "cohen_kappa": row["cohen_kappa"],
            "kappa_status": row["kappa_status"],
            "design_weighted_exact_agreement_percent": (
                f"{float(row['design_weighted_exact_agreement_percent']):.2f}"
            ),
            "design_weighted_ci95_low_percent": (
                f"{float(row['design_weighted_exact_agreement_ci95_low_percent']):.2f}"
            ),
            "design_weighted_ci95_high_percent": (
                f"{float(row['design_weighted_exact_agreement_ci95_high_percent']):.2f}"
            ),
        }
        for row in human_rq1_agreement
    ]
    if len(human_rq1_agreement_rows) != 27:
        raise ValueError("Expected 27 conditional RQ1 agreement rows")

    human_rq1_confusion_rows = [
        {
            "scope": rq1_scope_labels[row["scope_id"]],
            "measure": rq1_measure_labels[row["measure_id"]],
            "comparison": rq1_pair_labels[row["pair_id"]],
            "first_value": row["first_value"],
            "second_value": row["second_value"],
            "records": int(row["records_n"]),
            "cell_n": int(row["cell_n"]),
            "unweighted_cell_percent": (
                f"{float(row['cell_percent']):.2f}"
            ),
            "design_weighted_cell_percent": (
                f"{float(row['design_weighted_cell_percent']):.2f}"
            ),
        }
        for row in human_rq1_confusion
    ]
    if len(human_rq1_confusion_rows) != human_rq1_manifest["counts"][
        "pairwise_confusion_rows"
    ]:
        raise ValueError("Conditional RQ1 confusion-row count drifted")

    return {
        "table_1_corpus_formation.csv": formation,
        "table_2_target_actor_visibility.csv": visibility,
        "table_3_cross_stage_consistency.csv": consistency,
        "table_4_supporting_and_sensitivity.csv": supporting,
        "supplement_s1a_heuristic_proportions.csv": heuristic_proportions,
        "supplement_s1b_paired_heuristics.csv": paired_heuristics,
        "supplement_s2a_sampling_composition.csv": scope_rows(sampling_scopes),
        "supplement_s2b_quality_scopes.csv": scope_rows(quality_scopes),
        "supplement_s2c_sensitivity_ranges.csv": sensitivity_ranges,
        "supplement_s2d_authorization_composition.csv": authorization_composition,
        "supplement_s3_cross_model_field_agreement.csv": cross_model_agreement,
        "supplement_s4_human_audit_field_agreement.csv": human_audit_field_rows,
        "supplement_s4_human_audit_pair_summary.csv": human_audit_pair_rows,
        "supplement_s4c_human_audit_rq1_estimates.csv": human_rq1_estimate_rows,
        "supplement_s4d_human_audit_rq1_paired_cells.csv": human_rq1_cell_rows,
        "supplement_s4e_human_audit_rq1_agreement.csv": human_rq1_agreement_rows,
        "supplement_s4f_human_audit_rq1_confusion.csv": human_rq1_confusion_rows,
    }, input_hashes


def table_notes() -> str:
    return """# Manuscript table notes (v1)

These tables were generated only from checksum-verified public aggregate files.
They contain no title text, source name, URL, record identifier, or evidence span.

**General note.** The unit is one unique final de-identified title text, identified
by its title hash, not a source record, film, scene, event, person, producer, or
viewer. The analytical sample contains 38,298 distinct title hashes. All proportions
describe this retained sample; they do not estimate the 89,161-text candidate frame
or all Chinese adult-film titles.

**Table 1.** Inclusion required a contract-valid v0.3 output with
`record_validity=valid`, followed by one label-independent representative per
final-text hash, reapplied after exact-span privacy remediation. The corpus combines
36,303 probability-track texts and 1,995
mechanism-enriched texts. The analysis describes these texts with equal
unit weight.

**Table 2.** The denominator is unique title texts with a visible distribution-stage
opportunity. “Visible” means membership in the contract-defined visible-position
set; `not_visible`, `unclear`, and `not_applicable` fall outside that set. The
percentage-point contrast is paired within title. An unexpressed or unclear actor
position is not evidence that the actor was male, responsible, intentionally hidden,
or legally culpable.

**Table 3.** Consistency is exact equality of coded target-position labels among
texts eligible for each stage comparison. This is a within-instrument comparison,
not an independent validation; it does not establish that stages concern the same
real person, event, or file.

**Table 4.** Strict feminine and `mixed_multiple` remain separate categories.
The four person-position fields are desire, pleasure, refusal/resistance, and
attributed speech; they are not an exhaustive measure of agency or subjectivity.
“No feminine coding” does not necessarily mean that all four fields are
`not_visible`; a field may contain a non-feminine contract value.
Authorization rows are deterministic lexical-boundary sensitivities, not claims
about actual authorization or consent. The paper-corpus-scoped config at
`config/codex_authorization_boundary_sensitivity_v1.json` and the archived analysis
manifest; recalculation does not overwrite source labels.

**Supplement S1.** Wilson intervals and paired-difference intervals are retained
only as conventional unique-title-text heuristics under approximate title
independence. Exact McNemar p values are omitted because the paired cells and
differences fully describe the retained analytical sample. There is no defined
external superpopulation and no multiplicity decision.
They are not used for significance claims and do not expand the inferential target.

**Supplement S2.** Exact scope tables and compact ranges summarize all versioned
sampling-composition, quality, leave-one-source, and leave-one-length-band
variants. The five composition scopes are the full analytical sample, all probability
tracks, the later-sequence probability track, the frozen-layer probability track,
and the frozen-layer mechanism-enriched track. The four quality rows are the
full analytical sample and three overlapping restrictions: no adjudication flag,
evidence-complete, and no normalization action. The `above 50%` and `above zero`
rules are descriptive direction checks recorded in analysis v1 before the full
manuscript draft; they are neither preregistered hypotheses nor significance tests.

**Supplement S3.** Field-level exact agreement and Cohen's kappa compare the
frozen DeepSeek v0.3 labels with one blinded Codex coding pass on a purposively
stratified 100-record benchmark. Codex is not a human coder or gold standard, and
these diagnostics do not estimate corpus-wide field accuracy. A blank kappa is
undefined because a marginal lacked variation; zero or negative kappa can arise
under marginal imbalance or a prevalence paradox and must be read alongside exact
agreement rather than as an accuracy score.

**Supplement S4.** Two distinct nonresearcher coders independently coded the same
fixed 400-record, three-stratum sample across 16 semantic fields. Both encountered
the sampled titles for the first time and could not see the AI labels. They used
the same review application, semantic fields, option codes, and submission
contract, with separately randomized order. A deleted researcher interface pilot
contributes zero records. Tables S4A and S4B report all-400 field summaries;
Tables S4C--S4F report end-to-end RQ1 estimates, paired cells, conditional
agreement, and confusion matrices. These comparisons assess AI coding accuracy
against independent human reference judgments and do not overwrite the
38,298-text analytical sample.
"""


def main_tables_markdown(
    tables: dict[str, list[dict[str, object]]],
) -> str:
    def display(rows: list[dict[str, object]]) -> list[dict[str, object]]:
        return [
            {
                key: f"{value:,}"
                if key.endswith("_n") and isinstance(value, int)
                else value
                for key, value in row.items()
            }
            for row in rows
        ]

    formation = markdown_table(
        display(tables["table_1_corpus_formation.csv"]),
        (
            ("stage_or_component", "Stage or component"),
            ("included_n", "Included, n"),
            ("excluded_n", "Excluded, n"),
            ("status_or_rule", "Status or rule"),
        ),
    )
    visibility = markdown_table(
        display(tables["table_2_target_actor_visibility.csv"]),
        (
            ("measure", "Measure"),
            ("selected_n", "Selected, n"),
            ("denominator_n", "Eligible, n"),
            ("percent", "%"),
            ("paired_cell_or_note", "Paired cell or note"),
        ),
    )
    consistency = markdown_table(
        display(tables["table_3_cross_stage_consistency.csv"]),
        (
            ("visible_stage_comparison", "Visible-stage comparison"),
            ("exactly_consistent_n", "Exactly consistent, n"),
            ("eligible_n", "Eligible, n"),
            ("percent", "%"),
            ("definition", "Definition"),
        ),
    )
    supporting = markdown_table(
        display(tables["table_4_supporting_and_sensitivity.csv"]),
        (
            ("analysis", "Question"),
            ("measure_or_boundary", "Measure or boundary"),
            ("selected_n", "Selected, n"),
            ("comparison_n", "Comparison, n"),
            ("eligible_n", "Eligible, n"),
            ("percent", "% or paired difference, pp"),
            ("manuscript_role", "Role"),
        ),
    )

    return f"""# Main Manuscript Tables v1

## Table 1. Corpus formation and analytical sample

{formation}

*Note.* The final estimand is the equal-weight description of the 38,298 retained
unique title texts, not the earlier candidate frame. The final excluded count is the
number of unique candidate texts outside the analytical sample.

## Table 2. Distribution-target and distribution-actor position visibility

{visibility}

*Note.* The opportunity denominator is 4,186 unique title texts with a visible
distribution event. “Visible” means membership in the contract-defined
visible-position set. The percentage-point row is a paired within-title
contrast; it is not a comparison of independent samples.

## Table 3. Secondary within-instrument target-label consistency across co-occurring stage fields

{consistency}

*Note.* Consistency is exact within-instrument equality of coded target-position
labels. It does not establish that stages concern the same real person, file,
event, or chronology.

## Table 4. Target composition, feminine person-position cues, and authorization-boundary sensitivity

{supporting}

*Note.* Distribution-target categories are mutually exclusive. `mixed_multiple`
is not reclassified as feminine. RQ4 is exploratory and compares deliberately
non-equivalent, non-exhaustive constructs; its paired cells are descriptive rather
than a common-scale effect. “No feminine coding” does not require all four fields to
equal `not_visible`. MQ1 rows classify title wording under alternative lexical
boundaries and do not measure actual authorization or consent.
"""


def render_all(
    analysis_dir: Path = DEFAULT_ANALYSIS_DIR,
    freeze_manifest_path: Path = DEFAULT_FREEZE_MANIFEST,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> dict[str, Any]:
    tables, input_hashes = build_tables(analysis_dir, freeze_manifest_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, str] = {}
    for filename, rows in tables.items():
        path = output_dir / filename
        write_csv(path, rows)
        outputs[relative(path)] = sha256_file(path)

    notes_path = output_dir / "table_notes_v1.md"
    notes_path.write_text(table_notes(), encoding="utf-8")
    outputs[relative(notes_path)] = sha256_file(notes_path)

    main_tables_path = output_dir / "main_tables_v1.md"
    main_tables_path.write_text(
        main_tables_markdown(tables),
        encoding="utf-8",
    )
    outputs[relative(main_tables_path)] = sha256_file(main_tables_path)

    manifest = {
        "created_at": utc_now(),
        "table_set_id": TABLE_SET_ID,
        "status": "privacy_remediated_v4_active_corpus_2026_08_23",
        "corpus_records": int(tables["table_1_corpus_formation.csv"][3]["included_n"]),
        "unit": "unique final de-identified title text",
        "distinct_title_hashes": int(
            tables["table_1_corpus_formation.csv"][3]["included_n"]
        ),
        "inputs": {**input_hashes, relative(SCRIPT): sha256_file(SCRIPT)},
        "parameters": {
            "unit_weight": 1,
            "intervals_in_manuscript_tables": False,
            "heuristic_intervals_in_supplement": True,
            "null_hypothesis_tests_in_supplement": False,
            "privacy": "aggregate-only",
        },
        "outputs": outputs,
        "api_calls": 0,
        "raw_data_files_read": 0,
        "private_title_level_files_read": 0,
        "frozen_outputs_rewritten": 0,
    }
    manifest_path = output_dir / f"{TABLE_SET_ID}_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis-dir", type=Path, default=DEFAULT_ANALYSIS_DIR)
    parser.add_argument("--freeze-manifest", type=Path, default=DEFAULT_FREEZE_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    manifest = render_all(args.analysis_dir, args.freeze_manifest, args.output_dir)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
