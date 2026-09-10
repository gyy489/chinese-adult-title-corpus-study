"""Estimate the RQ1 target--actor contrast in the 400-record human audit.

The private row-level inputs are read without modification.  Published outputs
contain aggregate counts and design-weighted estimates only; title text, blind
identifiers, record identifiers/keys, and reviewer notes are never written.

This module complements, rather than replaces, the all-field agreement summary
in ``summarize_gendered_visibility_human_audit_400_v1.py``.  Its purpose is to
audit the manuscript's primary paired estimand on its conditional denominator.
"""

from __future__ import annotations

import csv
import json
import math
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.analysis.summarize_gendered_visibility_human_audit_400_v1 import (
    BASE_CODEBOOK,
    EXPECTED_CORPUS_RECORDS,
    EXPECTED_RECORDS,
    RECHECK_CODEBOOK,
    REVIEW_MANIFEST,
    REVIEWER_DATABASES,
    ROOT,
    SAMPLING_FRAME,
    SEALED_REFERENCE,
    cohen_kappa,
    load_ai_reference,
    load_codebook_contract,
    load_reviewer_snapshot,
    sha256_file,
)

OUTPUT_DIR = ROOT / "data/processed/gendered_visibility_human_audit_rq1_v1"
SCRIPT = Path(__file__).resolve()
UPSTREAM_AGGREGATOR = (
    ROOT / "src/analysis/summarize_gendered_visibility_human_audit_400_v1.py"
)
ANALYSIS_DATE = "2026-08-19"
ANALYSIS_ID = "gendered_visibility_human_audit_rq1_v1"
Z_95 = 1.959963984540054

VISIBLE_POSITIONS = frozenset(
    {
        "feminine",
        "masculine",
        "mixed_multiple",
        "gender_diverse",
        "gender_unspecified",
        "mutual_or_reciprocal",
    }
)
SOURCE_ORDER = (
    "frozen_ai_v0_3",
    "reviewer_1_recheck_v2",
    "reviewer_2_v1",
)
SOURCE_INSTRUCTION_VERSION = {
    "frozen_ai_v0_3": "frozen_ai_contract_v0.3",
    "reviewer_1_recheck_v2": "v2.0.0_direct_guidance_recheck",
    "reviewer_2_v1": "v1.0.0_original_guidance",
}
PAIR_DEFINITIONS = (
    ("ai_vs_reviewer_1_recheck", "frozen_ai_v0_3", "reviewer_1_recheck_v2"),
    ("ai_vs_reviewer_2", "frozen_ai_v0_3", "reviewer_2_v1"),
    (
        "reviewer_1_recheck_vs_reviewer_2",
        "reviewer_1_recheck_v2",
        "reviewer_2_v1",
    ),
)
POSITION_FIELDS = {
    "target": "distribution_target_position",
    "actor": "distribution_actor_position",
}
SCOPE_DEFINITIONS = {
    "source_specific_eligibility": (
        "each source's own valid + privacy-relevant + visible-distribution set"
    ),
    "ai_reference_eligible": (
        "records the frozen AI coded valid + privacy-relevant + visible-distribution"
    ),
    "all_three_eligible": (
        "records all three sources coded valid + privacy-relevant + visible-distribution"
    ),
}


@dataclass(frozen=True)
class SampleDesign:
    """Fixed stratified-SRS audit design keyed internally by blind identifier."""

    ids: tuple[str, ...]
    weights: dict[str, float]
    strata: dict[str, str]
    frame_n: dict[str, int]
    sample_n: dict[str, int]


@dataclass(frozen=True)
class RatioEstimate:
    """Ratio of HT domain totals and its linearization uncertainty."""

    domain_n: int
    weighted_domain_total: float
    weighted_outcome_total: float
    estimate: float
    standard_error: float
    ci95_low: float
    ci95_high: float


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"refusing to write empty table: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(rows[0]),
            extrasaction="raise",
        )
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_design() -> SampleDesign:
    rows = read_csv(SAMPLING_FRAME)
    if len(rows) != EXPECTED_RECORDS:
        raise RuntimeError(
            f"sampling frame has {len(rows)} rows, expected {EXPECTED_RECORDS}"
        )
    if len({row["blind_id"] for row in rows}) != EXPECTED_RECORDS:
        raise RuntimeError("sampling-frame blind identifiers are not unique")

    design_manifest = json.loads(REVIEW_MANIFEST.read_text(encoding="utf-8"))
    weights: dict[str, float] = {}
    strata: dict[str, str] = {}
    frame_n: dict[str, int] = {}
    sample_n: dict[str, int] = {}
    observed_sample_n: Counter[str] = Counter()
    for row in rows:
        blind_id = row["blind_id"]
        stratum = row["audit_stratum"]
        weights[blind_id] = float(row["design_weight"])
        strata[blind_id] = stratum
        observed_sample_n[stratum] += 1
        row_frame_n = int(row["stratum_frame_n"])
        row_sample_n = int(row["stratum_sample_n"])
        if stratum in frame_n and frame_n[stratum] != row_frame_n:
            raise RuntimeError(f"inconsistent frame size in stratum {stratum}")
        if stratum in sample_n and sample_n[stratum] != row_sample_n:
            raise RuntimeError(f"inconsistent sample size in stratum {stratum}")
        frame_n[stratum] = row_frame_n
        sample_n[stratum] = row_sample_n
        expected_weight = row_frame_n / row_sample_n
        if not math.isclose(weights[blind_id], expected_weight, abs_tol=1e-9):
            raise RuntimeError(f"design weight drifted for stratum {stratum}")

    if dict(observed_sample_n) != sample_n:
        raise RuntimeError("observed stratum sample sizes differ from frozen design")
    if sample_n != design_manifest["quotas"]:
        raise RuntimeError("sampling quotas differ from the frozen review manifest")
    if sum(frame_n.values()) != EXPECTED_CORPUS_RECORDS:
        raise RuntimeError("stratum frames do not recover the manuscript corpus")
    if not math.isclose(
        sum(weights.values()), EXPECTED_CORPUS_RECORDS, abs_tol=1e-6
    ):
        raise RuntimeError("design weights do not recover the manuscript corpus")

    return SampleDesign(
        ids=tuple(sorted(weights)),
        weights=weights,
        strata=strata,
        frame_n=dict(sorted(frame_n.items())),
        sample_n=dict(sorted(sample_n.items())),
    )


def stratified_ratio_estimate(
    design: SampleDesign,
    domain: Mapping[str, bool],
    outcome: Mapping[str, float],
    *,
    bounds: tuple[float, float],
) -> RatioEstimate:
    """Estimate a domain mean with a stratified ratio linearization CI.

    ``domain`` is the conditional analysis opportunity. ``outcome`` may be a
    binary indicator or the paired target-minus-actor difference (-1, 0, 1).
    The variance uses the usual SRS-without-replacement linearization residual
    within each frozen design stratum and a normal 95% interval.
    """

    expected_ids = set(design.ids)
    if set(domain) != expected_ids or set(outcome) != expected_ids:
        raise ValueError("domain and outcome must cover the complete sample design")
    domain_n = sum(bool(domain[item_id]) for item_id in design.ids)
    weighted_domain_total = sum(
        design.weights[item_id]
        for item_id in design.ids
        if domain[item_id]
    )
    if weighted_domain_total <= 0:
        raise ValueError("ratio domain is empty")
    weighted_outcome_total = sum(
        design.weights[item_id] * float(outcome[item_id])
        for item_id in design.ids
        if domain[item_id]
    )
    estimate = weighted_outcome_total / weighted_domain_total

    variance_total = 0.0
    for stratum, n_h in design.sample_n.items():
        stratum_ids = [
            item_id
            for item_id in design.ids
            if design.strata[item_id] == stratum
        ]
        if len(stratum_ids) != n_h or n_h <= 1:
            raise RuntimeError(f"invalid variance stratum {stratum}")
        residuals = [
            (float(outcome[item_id]) - estimate) if domain[item_id] else 0.0
            for item_id in stratum_ids
        ]
        residual_mean = sum(residuals) / n_h
        residual_variance = sum(
            (value - residual_mean) ** 2 for value in residuals
        ) / (n_h - 1)
        n_frame = design.frame_n[stratum]
        sampling_fraction = n_h / n_frame
        variance_total += (
            n_frame**2
            * (1.0 - sampling_fraction)
            * residual_variance
            / n_h
        )
    standard_error = math.sqrt(max(0.0, variance_total)) / weighted_domain_total
    lower_bound, upper_bound = bounds
    ci95_low = max(lower_bound, estimate - Z_95 * standard_error)
    ci95_high = min(upper_bound, estimate + Z_95 * standard_error)
    return RatioEstimate(
        domain_n=domain_n,
        weighted_domain_total=weighted_domain_total,
        weighted_outcome_total=weighted_outcome_total,
        estimate=estimate,
        standard_error=standard_error,
        ci95_low=ci95_low,
        ci95_high=ci95_high,
    )


def eligible(annotation: Mapping[str, str]) -> bool:
    return (
        annotation["record_validity"] == "valid"
        and annotation["privacy_chain_relevance"] == "relevant"
        and annotation["distribution_visibility"] == "visible"
    )


def position_visible(annotation: Mapping[str, str], field: str) -> bool:
    return annotation[field] in VISIBLE_POSITIONS


def format_float(value: float, digits: int = 6) -> str:
    return f"{value:.{digits}f}"


def percent(value: float) -> str:
    return format_float(value * 100.0)


def scope_masks(
    design: SampleDesign,
    sources: Mapping[str, Mapping[str, Mapping[str, str]]],
) -> dict[str, dict[str, bool]]:
    ai_mask = {
        item_id: eligible(sources["frozen_ai_v0_3"][item_id])
        for item_id in design.ids
    }
    common_mask = {
        item_id: all(eligible(sources[source][item_id]) for source in SOURCE_ORDER)
        for item_id in design.ids
    }
    masks = {
        "ai_reference_eligible": ai_mask,
        "all_three_eligible": common_mask,
    }
    if not any(ai_mask.values()) or not any(common_mask.values()):
        raise RuntimeError("frozen RQ1 sensitivity scope is empty")
    return masks


def source_scope_mask(
    scope_id: str,
    source_id: str,
    design: SampleDesign,
    sources: Mapping[str, Mapping[str, Mapping[str, str]]],
    fixed_masks: Mapping[str, Mapping[str, bool]],
) -> dict[str, bool]:
    if scope_id == "source_specific_eligibility":
        return {
            item_id: eligible(sources[source_id][item_id])
            for item_id in design.ids
        }
    return dict(fixed_masks[scope_id])


def build_estimate_rows(
    design: SampleDesign,
    sources: Mapping[str, Mapping[str, Mapping[str, str]]],
    fixed_masks: Mapping[str, Mapping[str, bool]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    estimate_rows: list[dict[str, Any]] = []
    cell_rows: list[dict[str, Any]] = []
    for scope_id, scope_definition in SCOPE_DEFINITIONS.items():
        for source_id in SOURCE_ORDER:
            domain = source_scope_mask(
                scope_id, source_id, design, sources, fixed_masks
            )
            target = {
                item_id: float(
                    position_visible(
                        sources[source_id][item_id], POSITION_FIELDS["target"]
                    )
                )
                for item_id in design.ids
            }
            actor = {
                item_id: float(
                    position_visible(
                        sources[source_id][item_id], POSITION_FIELDS["actor"]
                    )
                )
                for item_id in design.ids
            }
            difference = {
                item_id: target[item_id] - actor[item_id]
                for item_id in design.ids
            }
            target_estimate = stratified_ratio_estimate(
                design, domain, target, bounds=(0.0, 1.0)
            )
            actor_estimate = stratified_ratio_estimate(
                design, domain, actor, bounds=(0.0, 1.0)
            )
            difference_estimate = stratified_ratio_estimate(
                design, domain, difference, bounds=(-1.0, 1.0)
            )
            scope_ids = [item_id for item_id in design.ids if domain[item_id]]
            target_n = sum(int(target[item_id]) for item_id in scope_ids)
            actor_n = sum(int(actor[item_id]) for item_id in scope_ids)
            scope_n = len(scope_ids)
            estimate_rows.append(
                {
                    "scope_id": scope_id,
                    "scope_definition": scope_definition,
                    "source_id": source_id,
                    "source_instruction_version": SOURCE_INSTRUCTION_VERSION[
                        source_id
                    ],
                    "unweighted_scope_n": scope_n,
                    "design_weighted_scope_total": format_float(
                        target_estimate.weighted_domain_total
                    ),
                    "unweighted_target_visible_n": target_n,
                    "unweighted_target_visible_percent": format_float(
                        target_n / scope_n * 100.0
                    ),
                    "design_weighted_target_visible_total": format_float(
                        target_estimate.weighted_outcome_total
                    ),
                    "design_weighted_target_visible_percent": percent(
                        target_estimate.estimate
                    ),
                    "design_weighted_target_visible_se_pp": percent(
                        target_estimate.standard_error
                    ),
                    "design_weighted_target_visible_ci95_low_percent": percent(
                        target_estimate.ci95_low
                    ),
                    "design_weighted_target_visible_ci95_high_percent": percent(
                        target_estimate.ci95_high
                    ),
                    "unweighted_actor_visible_n": actor_n,
                    "unweighted_actor_visible_percent": format_float(
                        actor_n / scope_n * 100.0
                    ),
                    "design_weighted_actor_visible_total": format_float(
                        actor_estimate.weighted_outcome_total
                    ),
                    "design_weighted_actor_visible_percent": percent(
                        actor_estimate.estimate
                    ),
                    "design_weighted_actor_visible_se_pp": percent(
                        actor_estimate.standard_error
                    ),
                    "design_weighted_actor_visible_ci95_low_percent": percent(
                        actor_estimate.ci95_low
                    ),
                    "design_weighted_actor_visible_ci95_high_percent": percent(
                        actor_estimate.ci95_high
                    ),
                    "unweighted_target_minus_actor_pp": format_float(
                        (target_n - actor_n) / scope_n * 100.0
                    ),
                    "design_weighted_target_minus_actor_pp": percent(
                        difference_estimate.estimate
                    ),
                    "design_weighted_target_minus_actor_se_pp": percent(
                        difference_estimate.standard_error
                    ),
                    "design_weighted_target_minus_actor_ci95_low_pp": percent(
                        difference_estimate.ci95_low
                    ),
                    "design_weighted_target_minus_actor_ci95_high_pp": percent(
                        difference_estimate.ci95_high
                    ),
                    "interval_method": (
                        "stratified_srswor_ratio_linearization_normal_95_with_fpc"
                    ),
                }
            )

            cell_definitions = (
                ("target_visible_actor_outside", 1.0, 0.0),
                ("target_outside_actor_visible", 0.0, 1.0),
                ("both_visible", 1.0, 1.0),
                ("neither_visible", 0.0, 0.0),
            )
            for cell_id, target_value, actor_value in cell_definitions:
                cell = {
                    item_id: float(
                        target[item_id] == target_value
                        and actor[item_id] == actor_value
                    )
                    for item_id in design.ids
                }
                cell_estimate = stratified_ratio_estimate(
                    design, domain, cell, bounds=(0.0, 1.0)
                )
                cell_n = sum(int(cell[item_id]) for item_id in scope_ids)
                cell_rows.append(
                    {
                        "scope_id": scope_id,
                        "source_id": source_id,
                        "cell_id": cell_id,
                        "unweighted_scope_n": scope_n,
                        "unweighted_cell_n": cell_n,
                        "unweighted_cell_percent": format_float(
                            cell_n / scope_n * 100.0
                        ),
                        "design_weighted_scope_total": format_float(
                            cell_estimate.weighted_domain_total
                        ),
                        "design_weighted_cell_total": format_float(
                            cell_estimate.weighted_outcome_total
                        ),
                        "design_weighted_cell_percent": percent(
                            cell_estimate.estimate
                        ),
                    }
                )
    return estimate_rows, cell_rows


def agreement_scope_mask(
    scope_id: str,
    design: SampleDesign,
    fixed_masks: Mapping[str, Mapping[str, bool]],
) -> dict[str, bool]:
    if scope_id == "full_sample":
        return {item_id: True for item_id in design.ids}
    return dict(fixed_masks[scope_id])


def values_for_measure(
    source_id: str,
    measure_id: str,
    design: SampleDesign,
    sources: Mapping[str, Mapping[str, Mapping[str, str]]],
) -> dict[str, str]:
    annotations = sources[source_id]
    if measure_id == "eligibility_binary":
        return {
            item_id: "eligible" if eligible(annotations[item_id]) else "outside_eligibility"
            for item_id in design.ids
        }
    role, resolution = measure_id.split("_", maxsplit=1)
    field = POSITION_FIELDS[role]
    if resolution == "visibility_binary":
        return {
            item_id: (
                "visible"
                if position_visible(annotations[item_id], field)
                else "outside_visible_set"
            )
            for item_id in design.ids
        }
    if resolution == "position_exact":
        return {item_id: annotations[item_id][field] for item_id in design.ids}
    raise ValueError(f"unsupported agreement measure: {measure_id}")


def build_agreement_rows(
    design: SampleDesign,
    sources: Mapping[str, Mapping[str, Mapping[str, str]]],
    fixed_masks: Mapping[str, Mapping[str, bool]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    agreement_rows: list[dict[str, Any]] = []
    confusion_rows: list[dict[str, Any]] = []
    measure_scopes = [
        ("full_sample", "eligibility_binary"),
        ("ai_reference_eligible", "target_visibility_binary"),
        ("ai_reference_eligible", "actor_visibility_binary"),
        ("ai_reference_eligible", "target_position_exact"),
        ("ai_reference_eligible", "actor_position_exact"),
        ("all_three_eligible", "target_visibility_binary"),
        ("all_three_eligible", "actor_visibility_binary"),
        ("all_three_eligible", "target_position_exact"),
        ("all_three_eligible", "actor_position_exact"),
    ]
    for scope_id, measure_id in measure_scopes:
        domain = agreement_scope_mask(scope_id, design, fixed_masks)
        scoped_ids = [item_id for item_id in design.ids if domain[item_id]]
        for pair_id, first_source, second_source in PAIR_DEFINITIONS:
            first = values_for_measure(
                first_source, measure_id, design, sources
            )
            second = values_for_measure(
                second_source, measure_id, design, sources
            )
            exact_indicator = {
                item_id: float(first[item_id] == second[item_id])
                for item_id in design.ids
            }
            exact = stratified_ratio_estimate(
                design, domain, exact_indicator, bounds=(0.0, 1.0)
            )
            exact_n = sum(int(exact_indicator[item_id]) for item_id in scoped_ids)
            kappa, kappa_status = cohen_kappa(
                [first[item_id] for item_id in scoped_ids],
                [second[item_id] for item_id in scoped_ids],
            )
            agreement_rows.append(
                {
                    "scope_id": scope_id,
                    "measure_id": measure_id,
                    "pair_id": pair_id,
                    "first_source": first_source,
                    "second_source": second_source,
                    "records_n": len(scoped_ids),
                    "exact_agreement_n": exact_n,
                    "exact_agreement_percent": format_float(
                        exact_n / len(scoped_ids) * 100.0
                    ),
                    "cohen_kappa": "" if kappa is None else format_float(kappa),
                    "kappa_status": kappa_status,
                    "design_weighted_records_total": format_float(
                        exact.weighted_domain_total
                    ),
                    "design_weighted_exact_agreement_percent": percent(
                        exact.estimate
                    ),
                    "design_weighted_exact_agreement_se_pp": percent(
                        exact.standard_error
                    ),
                    "design_weighted_exact_agreement_ci95_low_percent": percent(
                        exact.ci95_low
                    ),
                    "design_weighted_exact_agreement_ci95_high_percent": percent(
                        exact.ci95_high
                    ),
                    "interval_method": (
                        "stratified_srswor_ratio_linearization_normal_95_with_fpc"
                    ),
                    "kappa_weighting": "unweighted_fixed_audit_records",
                }
            )

            confusion: defaultdict[tuple[str, str], dict[str, float]] = defaultdict(
                lambda: {"n": 0.0, "weighted_total": 0.0}
            )
            for item_id in scoped_ids:
                cell = confusion[(first[item_id], second[item_id])]
                cell["n"] += 1
                cell["weighted_total"] += design.weights[item_id]
            for first_value, second_value in sorted(confusion):
                cell = confusion[(first_value, second_value)]
                confusion_rows.append(
                    {
                        "scope_id": scope_id,
                        "measure_id": measure_id,
                        "pair_id": pair_id,
                        "first_source": first_source,
                        "second_source": second_source,
                        "first_value": first_value,
                        "second_value": second_value,
                        "records_n": len(scoped_ids),
                        "cell_n": int(cell["n"]),
                        "cell_percent": format_float(
                            cell["n"] / len(scoped_ids) * 100.0
                        ),
                        "design_weighted_records_total": format_float(
                            exact.weighted_domain_total
                        ),
                        "design_weighted_cell_total": format_float(
                            cell["weighted_total"]
                        ),
                        "design_weighted_cell_percent": format_float(
                            cell["weighted_total"]
                            / exact.weighted_domain_total
                            * 100.0
                        ),
                    }
                )
    return agreement_rows, confusion_rows


def build(output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    fields, domains = load_codebook_contract()
    design = load_design()
    ai = load_ai_reference(fields, domains)
    reviewer_snapshots = {
        reviewer_id: load_reviewer_snapshot(
            reviewer_id, path, fields, domains
        )
        for reviewer_id, path in REVIEWER_DATABASES.items()
    }
    design_manifest = json.loads(REVIEW_MANIFEST.read_text(encoding="utf-8"))
    base_hash = sha256_file(BASE_CODEBOOK)
    recheck_hash = sha256_file(RECHECK_CODEBOOK)
    queue_hash = design_manifest["outputs"][
        "data/interim/72_gendered_visibility_human_review_400_v1/private/blind_queue.csv"
    ]
    for reviewer_id, snapshot in reviewer_snapshots.items():
        if snapshot.metadata.get("queue_sha256") != queue_hash:
            raise RuntimeError(f"{reviewer_id} queue contract drifted")
        if snapshot.metadata.get("review_contract") != (
            "gendered_visibility_paper_core_v1"
        ):
            raise RuntimeError(f"{reviewer_id} semantic review contract drifted")
    reviewer_1 = reviewer_snapshots["reviewer_1"]
    reviewer_2 = reviewer_snapshots["reviewer_2"]
    if reviewer_1.metadata.get("codebook_sha256") != recheck_hash:
        raise RuntimeError("reviewer 1 recheck did not use frozen v2 guidance")
    if reviewer_1.metadata.get("base_codebook_sha256") != base_hash:
        raise RuntimeError("reviewer 1 base semantic contract drifted")
    if reviewer_2.metadata.get("codebook_sha256") != base_hash:
        raise RuntimeError("reviewer 2 did not use frozen v1 guidance")

    expected_ids = set(design.ids)
    if set(ai) != expected_ids:
        raise RuntimeError("AI and sample-design record sets differ")
    for snapshot in reviewer_snapshots.values():
        if set(snapshot.annotations) != expected_ids:
            raise RuntimeError(f"{snapshot.reviewer_id} record set differs")

    sources: dict[str, Mapping[str, Mapping[str, str]]] = {
        "frozen_ai_v0_3": ai,
        "reviewer_1_recheck_v2": reviewer_1.annotations,
        "reviewer_2_v1": reviewer_2.annotations,
    }
    fixed_masks = scope_masks(design, sources)
    estimate_rows, paired_cell_rows = build_estimate_rows(
        design, sources, fixed_masks
    )
    agreement_rows, confusion_rows = build_agreement_rows(
        design, sources, fixed_masks
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    estimate_path = output_dir / "rq1_estimates.csv"
    paired_cell_path = output_dir / "rq1_paired_cells.csv"
    agreement_path = output_dir / "rq1_pairwise_agreement.csv"
    confusion_path = output_dir / "rq1_pairwise_confusion.csv"
    provenance_path = output_dir / "provenance.json"
    readme_path = output_dir / "README.md"
    manifest_path = output_dir / "manifest.json"
    write_csv(estimate_path, estimate_rows)
    write_csv(paired_cell_path, paired_cell_rows)
    write_csv(agreement_path, agreement_rows)
    write_csv(confusion_path, confusion_rows)

    source_specific_n = {
        source: sum(
            eligible(sources[source][item_id]) for item_id in design.ids
        )
        for source in SOURCE_ORDER
    }
    fixed_scope_n = {
        scope: sum(mask.values()) for scope, mask in fixed_masks.items()
    }
    provenance = {
        "analysis_date": ANALYSIS_DATE,
        "analysis_id": ANALYSIS_ID,
        "audit_role": "primary_estimand_quality_audit_not_gold_standard",
        "automated_labels_modified": False,
        "adjudication": "none",
        "corpus": "gendered_visibility_paper_corpus_v1",
        "corpus_records": EXPECTED_CORPUS_RECORDS,
        "rq1_eligibility": {
            "record_validity": "valid",
            "privacy_chain_relevance": "relevant",
            "distribution_visibility": "visible",
        },
        "visible_position_set": sorted(VISIBLE_POSITIONS),
        "scopes": SCOPE_DEFINITIONS,
        "sample": {
            "records": EXPECTED_RECORDS,
            "sampling_method": design_manifest["sampling_method"],
            "frame_n": design.frame_n,
            "sample_n": design.sample_n,
            "source_specific_eligible_n": source_specific_n,
            "fixed_scope_n": fixed_scope_n,
            "design_weight_total": format_float(sum(design.weights.values())),
        },
        "reviewer_profiles": {
            "reviewer_1": {
                "current_result": "same_400_record_recheck_under_v2",
                "instruction_version": SOURCE_INSTRUCTION_VERSION[
                    "reviewer_1_recheck_v2"
                ],
                "current_completed_records": EXPECTED_RECORDS,
                "prior_title_exposure": "same_400_records_seen_in_discarded_first_pass",
                "discarded_first_pass_result_status": (
                    "deleted_before_analysis_and_excluded_from_all_outputs"
                ),
                "discarded_first_pass_records_in_analysis": 0,
                "ai_comparison_exposure_before_recheck": (
                    "unverified_by_researcher"
                ),
                "first_exposure_independence_claim_permitted": False,
                "database_logical_snapshot_sha256": reviewer_1.logical_sha256,
                "earliest_update": reviewer_1.earliest_update,
                "latest_update": reviewer_1.latest_update,
            },
            "reviewer_2": {
                "current_result": "400_record_review_under_v1",
                "instruction_version": SOURCE_INSTRUCTION_VERSION[
                    "reviewer_2_v1"
                ],
                "current_completed_records": EXPECTED_RECORDS,
                "database_logical_snapshot_sha256": reviewer_2.logical_sha256,
                "earliest_update": reviewer_2.earliest_update,
                "latest_update": reviewer_2.latest_update,
            },
        },
        "uncertainty": {
            "point_estimator": (
                "ratio of Horvitz-Thompson domain-total estimators "
                "(Hájek-type design-weighted domain mean)"
            ),
            "variance": (
                "within-stratum SRSWOR ratio linearization with finite-population correction"
            ),
            "interval": "normal_95_clipped_to_parameter_bounds",
            "target_population": "the frozen 38,623-record manuscript corpus",
        },
        "privacy": {
            "aggregate_only": True,
            "contains_blind_id": False,
            "contains_record_id": False,
            "contains_record_key": False,
            "contains_title_text": False,
            "contains_reviewer_notes": False,
        },
        "interpretation_limits": [
            "Human coding is not treated as a gold standard or adjudicated truth.",
            "Source-specific eligibility estimates use different coder-defined domains and are not fixed-denominator agreement statistics.",
            "The AI-reference-eligible scope is an end-to-end fixed-denominator diagnostic; the all-three-eligible scope conditions on eligibility agreement and may look more concordant.",
            "Reviewer 1 re-reviewed the same 400 titles under clarified v2 guidance after a discarded first pass, so this is not an independent first-exposure replication.",
            "Reviewer 1 prior exposure to an AI comparison before the recheck is not verified and must not be assumed either way.",
            "Reviewer and instruction-version effects are confounded because reviewer 1 used v2 and reviewer 2 used v1.",
            "No discarded first-pass result is read, reconstructed, or included, and no frozen full-corpus AI label is overwritten.",
        ],
    }
    write_json(provenance_path, provenance)

    readme = """# RQ1 human-audit aggregate (v1)

This directory audits the manuscript's primary within-record comparison:
distribution-target visibility minus distribution-actor visibility. It is a new
versioned analysis and does not replace the earlier 16-field agreement summary.

The RQ1 opportunity requires `record_validity=valid`,
`privacy_chain_relevance=relevant`, and `distribution_visibility=visible`.
`rq1_estimates.csv` reports target visibility, actor visibility, and their paired
difference for each source's own eligibility set, the fixed AI eligibility set,
and the set deemed eligible by all three sources. `rq1_paired_cells.csv` reports
the four complete target/actor visibility configurations. The pairwise files
report eligibility agreement on all 400 records and binary-visibility plus exact
position-category agreement/confusion on the two fixed sensitivity scopes.

Weighted point estimates are ratios of Horvitz--Thompson domain-total estimators
(Hájek-type design-weighted domain means). Their 95% CIs use
within-stratum SRS-without-replacement ratio linearization, finite-population
corrections, and normal critical values. Cohen's kappa remains unweighted.

Reviewer 1's current database is a recheck of the same 400 titles under clarified
v2 guidance. The discarded first-pass result was deleted before this analysis;
zero first-pass records enter these outputs. Prior exposure to the title set is
therefore known, while any exposure to an AI comparison before the recheck is
currently unverified. Reviewer 2 used the original v1 guidance. The audit has no
adjudication, does not designate a human gold standard, and does not modify the
frozen full-corpus AI annotations.

Every published table is aggregate-only. No title, blind identifier, record
identifier/key, or reviewer note is present. Rebuild with:

```bash
scripts/evosci-python -m src.analysis.summarize_gendered_visibility_human_audit_rq1_v1
```
"""
    readme_path.write_text(readme, encoding="utf-8")

    input_files = (
        REVIEW_MANIFEST,
        SAMPLING_FRAME,
        SEALED_REFERENCE,
        BASE_CODEBOOK,
        RECHECK_CODEBOOK,
        UPSTREAM_AGGREGATOR,
        SCRIPT,
    )
    output_files = (
        estimate_path,
        paired_cell_path,
        agreement_path,
        confusion_path,
        provenance_path,
        readme_path,
    )
    manifest = {
        "analysis_date": ANALYSIS_DATE,
        "analysis_id": ANALYSIS_ID,
        "status": "complete_frozen_aggregate",
        "parameters": {
            "eligibility_fields": [
                "record_validity",
                "privacy_chain_relevance",
                "distribution_visibility",
            ],
            "position_fields": POSITION_FIELDS,
            "visible_position_set": sorted(VISIBLE_POSITIONS),
            "sources": list(SOURCE_ORDER),
            "scopes": list(SCOPE_DEFINITIONS),
            "pairs": [pair_id for pair_id, _, _ in PAIR_DEFINITIONS],
            "design_weighted": True,
            "confidence_level": 0.95,
            "variance_method": (
                "stratified_srswor_ratio_linearization_normal_with_fpc"
            ),
            "adjudication": "none",
            "discarded_reviewer_1_first_pass_included": False,
            "reviewer_1_ai_comparison_exposure_before_recheck": (
                "unverified_by_researcher"
            ),
        },
        "counts": {
            "corpus_records": EXPECTED_CORPUS_RECORDS,
            "sample_records": EXPECTED_RECORDS,
            "reviewer_1_recheck_records": EXPECTED_RECORDS,
            "reviewer_2_records": EXPECTED_RECORDS,
            "discarded_reviewer_1_first_pass_records_included": 0,
            "source_specific_eligible_n": source_specific_n,
            "fixed_scope_n": fixed_scope_n,
            "estimate_rows": len(estimate_rows),
            "paired_cell_rows": len(paired_cell_rows),
            "pairwise_agreement_rows": len(agreement_rows),
            "pairwise_confusion_rows": len(confusion_rows),
        },
        "inputs": {
            path.relative_to(ROOT).as_posix(): sha256_file(path)
            for path in input_files
        },
        "private_database_logical_snapshots": {
            REVIEWER_DATABASES[reviewer_id].relative_to(ROOT).as_posix(): {
                "checksum_kind": (
                    "sha256_canonical_reviewed_rows_excluding_title_and_timestamps"
                ),
                "sha256": snapshot.logical_sha256,
                "reviewed_records": EXPECTED_RECORDS,
            }
            for reviewer_id, snapshot in reviewer_snapshots.items()
        },
        "outputs": {path.name: sha256_file(path) for path in output_files},
    }
    write_json(manifest_path, manifest)
    return manifest


def main() -> None:
    manifest = build()
    print(
        json.dumps(
            {
                "analysis_id": manifest["analysis_id"],
                "sample_records": manifest["counts"]["sample_records"],
                "fixed_scope_n": manifest["counts"]["fixed_scope_n"],
                "output_directory": OUTPUT_DIR.relative_to(ROOT).as_posix(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
