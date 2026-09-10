#!/usr/bin/env python3
"""Analyze a versioned gendered-visibility manuscript corpus.

This is an offline, corpus-descriptive analysis. It verifies membership against
the immutable v0.3 annotation sources, joins only the metadata required for
auditable opportunity definitions and sensitivity checks, and emits aggregate
tables. It neither calls a provider nor rewrites frozen annotations.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scipy.stats import binomtest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = Path(__file__).resolve()
FREEZE_MANIFEST = (
    ROOT / "data/processed/gendered_visibility_paper_corpus_v1/manifest.json"
)
MEMBERSHIP = (
    ROOT
    / "data/processed/gendered_visibility_paper_corpus_v1/private/"
    "analytical_corpus_membership.csv"
)
FROZEN_RECORDS = (
    ROOT
    / "data/interim/51_gendered_argument_visibility_scaleup_5000_v1/private/"
    "validation_5000_final_records.jsonl"
)
FROZEN_SAMPLE = (
    ROOT
    / "data/interim/51_gendered_argument_visibility_scaleup_5000_v1/private/"
    "sample_manifest.csv"
)
RUN70_RECORDS = (
    ROOT
    / "data/interim/70_gendered_argument_visibility_budget_adaptive_20000_v1/"
    "private/final_records.jsonl"
)
RUN70_FRAME = (
    ROOT
    / "data/interim/70_gendered_argument_visibility_budget_adaptive_20000_v1/"
    "private/probability_sequence_85727.jsonl"
)
AUTHORIZATION_CONFIG = ROOT / "config/codex_authorization_boundary_sensitivity_v1.json"
DEFAULT_OUTPUT_DIR = (
    ROOT / "results/tables/gendered_visibility_paper_corpus_v1_analysis_v1"
)

EXPECTED_CORPUS_N = 38_623
Z_95 = 1.959963984540054
VISIBLE_POSITIONS = {
    "feminine",
    "masculine",
    "mixed_multiple",
    "gender_diverse",
    "gender_unspecified",
    "mutual_or_reciprocal",
}
PERSON_POSITION_FIELDS = (
    "desire_holder_position",
    "pleasure_holder_position",
    "refusal_resistance_position",
    "attributed_speech_position",
)


@dataclass(frozen=True)
class Unit:
    corpus_index: int
    source_layer: str
    sampling_track: str
    source_site: str
    length_band: str
    title: str
    title_sha256: str
    output: dict[str, Any]
    derived: dict[str, Any]
    normalization_actions: tuple[str, ...]


@dataclass(frozen=True)
class MetricSpec:
    candidate: str
    metric: str
    kind: str
    opportunity: Callable[[Unit], bool]
    selected: Callable[[Unit], bool]
    comparison: Callable[[Unit], bool] | None = None
    direction: str = "descriptive_only"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def record_key(record_id: str) -> str:
    return hashlib.sha256(record_id.encode("utf-8")).hexdigest()


def group_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at {path}:{line_number}") from exc


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0]) if rows else ["status"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def wilson_interval(selected: int, denominator: int) -> tuple[float, float]:
    if denominator <= 0:
        return math.nan, math.nan
    proportion = selected / denominator
    z2 = Z_95**2
    center = (proportion + z2 / (2 * denominator)) / (1 + z2 / denominator)
    half = (
        Z_95
        * math.sqrt(
            proportion * (1 - proportion) / denominator
            + z2 / (4 * denominator**2)
        )
        / (1 + z2 / denominator)
    )
    return max(0.0, center - half), min(1.0, center + half)


def paired_difference(
    selected: Sequence[bool], comparison: Sequence[bool]
) -> dict[str, float | int]:
    if len(selected) != len(comparison):
        raise ValueError("paired vectors differ in length")
    denominator = len(selected)
    if denominator == 0:
        return {
            "selected_n": 0,
            "comparison_selected_n": 0,
            "both_n": 0,
            "neither_n": 0,
            "denominator_n": 0,
            "estimate": math.nan,
            "ci95_low": math.nan,
            "ci95_high": math.nan,
            "mcnemar_exact_p": math.nan,
            "mcnemar_exact_log10_p": math.nan,
        }
    selected_only = sum(a and not b for a, b in zip(selected, comparison, strict=True))
    comparison_only = sum(
        b and not a for a, b in zip(selected, comparison, strict=True)
    )
    both = sum(a and b for a, b in zip(selected, comparison, strict=True))
    neither = denominator - selected_only - comparison_only - both
    differences = [int(a) - int(b) for a, b in zip(selected, comparison, strict=True)]
    estimate = sum(differences) / denominator
    if denominator > 1:
        squared = sum((value - estimate) ** 2 for value in differences)
        standard_error = math.sqrt(squared / (denominator - 1) / denominator)
    else:
        standard_error = 0.0
    discordant = selected_only + comparison_only
    p_value = (
        float(
            binomtest(
                min(selected_only, comparison_only),
                discordant,
                p=0.5,
                alternative="two-sided",
            ).pvalue
        )
        if discordant
        else 1.0
    )
    if discordant:
        tail = min(selected_only, comparison_only)
        log_terms = [
            math.lgamma(discordant + 1)
            - math.lgamma(index + 1)
            - math.lgamma(discordant - index + 1)
            - discordant * math.log(2)
            for index in range(tail + 1)
        ]
        maximum = max(log_terms)
        log_p = min(
            0.0,
            math.log(2)
            + maximum
            + math.log(sum(math.exp(value - maximum) for value in log_terms)),
        )
        log10_p = log_p / math.log(10)
    else:
        log10_p = 0.0
    return {
        "selected_n": selected_only,
        "comparison_selected_n": comparison_only,
        "both_n": both,
        "neither_n": neither,
        "denominator_n": denominator,
        "estimate": estimate,
        "ci95_low": max(-1.0, estimate - Z_95 * standard_error),
        "ci95_high": min(1.0, estimate + Z_95 * standard_error),
        "mcnemar_exact_p": p_value,
        "mcnemar_exact_log10_p": log10_p,
    }


def _source_freeze_for_input_verification(
    freeze: dict[str, Any], freeze_manifest_path: Path
) -> dict[str, Any]:
    """Resolve the manifest that directly pins the immutable annotation files.

    Privacy-remediated corpus versions form a checksum-verified successor chain
    rather than duplicating the historical source-file declarations.  Older
    corpus manifests already pin the annotation files directly, so they remain
    valid without a successor lookup.
    """

    direct_keys = {
        path.relative_to(ROOT).as_posix()
        for path in (FROZEN_RECORDS, FROZEN_SAMPLE, RUN70_RECORDS)
    }
    current = freeze
    current_path = freeze_manifest_path.resolve()
    seen = {current_path}
    while not direct_keys.issubset(current.get("inputs", {})):
        candidates = [
            ROOT / relative
            for relative in current.get("inputs", {})
            if relative.endswith("/manifest.json")
            and "paper_corpus" in relative
        ]
        if len(candidates) != 1:
            raise RuntimeError(
                "could not resolve exactly one predecessor paper-corpus manifest"
            )
        predecessor_path = candidates[0].resolve()
        if predecessor_path in seen:
            raise RuntimeError("paper-corpus manifest successor chain is cyclic")
        key = predecessor_path.relative_to(ROOT).as_posix()
        if sha256_file(predecessor_path) != current["inputs"].get(key):
            raise RuntimeError("predecessor paper-corpus manifest checksum mismatch")
        seen.add(predecessor_path)
        current = json.loads(predecessor_path.read_text(encoding="utf-8"))
    return current


def _verify_input_hashes(
    freeze: dict[str, Any],
    freeze_manifest_path: Path,
    membership_path: Path = MEMBERSHIP,
) -> None:
    source_freeze = _source_freeze_for_input_verification(
        freeze, freeze_manifest_path
    )
    expected_inputs = source_freeze["inputs"]
    paths = (FROZEN_RECORDS, FROZEN_SAMPLE, RUN70_RECORDS)
    for path in paths:
        key = path.relative_to(ROOT).as_posix()
        observed = sha256_file(path)
        if expected_inputs.get(key) != observed:
            raise RuntimeError(f"frozen input checksum mismatch: {key}")
    membership_key = membership_path.relative_to(ROOT).as_posix()
    if freeze["outputs"].get(membership_key) != sha256_file(membership_path):
        raise RuntimeError("paper-corpus membership checksum mismatch")


def resolve_title_overrides_path(
    freeze: dict[str, Any], freeze_manifest_path: Path
) -> Path | None:
    matches = [
        ROOT / relative
        for relative in freeze.get("outputs", {})
        if relative.endswith("/private/title_overrides.csv")
    ]
    if not matches:
        return None
    if len(matches) != 1:
        raise RuntimeError("freeze manifest declares multiple title override files")
    path = matches[0]
    expected = freeze["outputs"][path.relative_to(ROOT).as_posix()]
    if sha256_file(path) != expected:
        raise RuntimeError("paper-corpus title override checksum mismatch")
    if freeze_manifest_path.parent not in path.parents:
        raise RuntimeError("title override is outside the frozen corpus directory")
    return path


def _valid_output(row: dict[str, Any]) -> bool:
    output = row.get("output")
    return (
        row.get("valid") is True
        and isinstance(output, dict)
        and output.get("record_validity") == "valid"
    )


def load_units(
    freeze_manifest_path: Path = FREEZE_MANIFEST,
    membership_path: Path = MEMBERSHIP,
    expected_corpus_n: int = EXPECTED_CORPUS_N,
    *,
    require_exact_valid_output_membership: bool = True,
    title_overrides_path: Path | None = None,
) -> tuple[list[Unit], dict[str, Any]]:
    freeze = json.loads(freeze_manifest_path.read_text(encoding="utf-8"))
    _verify_input_hashes(freeze, freeze_manifest_path, membership_path)
    declared_override_path = resolve_title_overrides_path(freeze, freeze_manifest_path)
    if title_overrides_path is not None:
        title_overrides_path = title_overrides_path.resolve()
        if declared_override_path is None or title_overrides_path != declared_override_path.resolve():
            raise RuntimeError("requested title override is not pinned by freeze manifest")
    else:
        title_overrides_path = declared_override_path
    freeze_corpus_n = freeze["scope"].get(
        "paper_analytical_corpus_unique_final_texts",
        freeze["scope"].get(
            "unique_final_deidentified_title_texts",
            freeze["scope"].get("paper_analytical_corpus_records"),
        ),
    )
    if freeze_corpus_n != expected_corpus_n:
        raise RuntimeError("freeze manifest corpus count drifted")

    membership = read_csv(membership_path)
    if len(membership) != expected_corpus_n:
        raise RuntimeError("membership count drifted")
    if [int(row["corpus_index"]) for row in membership] != list(
        range(1, expected_corpus_n + 1)
    ):
        raise RuntimeError("membership corpus indices are not contiguous")
    if len({row["record_key_sha256"] for row in membership}) != len(membership):
        raise RuntimeError("duplicate record key found in frozen paper corpus")

    frozen_meta = {int(row["item_index"]): row for row in read_csv(FROZEN_SAMPLE)}
    run70_meta = {
        int(row["sample_item_index"]): row for row in read_jsonl(RUN70_FRAME)
    }
    frozen_results = {int(row["item_index"]): row for row in read_jsonl(FROZEN_RECORDS)}
    run70_results = {
        int(row["sample_item_index"]): row for row in read_jsonl(RUN70_RECORDS)
    }

    reconstructed_members: set[tuple[str, int]] = set()
    for index, row in frozen_results.items():
        if _valid_output(row):
            reconstructed_members.add(("frozen_v0_3_5000", index))
    for index, row in run70_results.items():
        if row.get("status") == "complete" and _valid_output(row):
            reconstructed_members.add(("run70_complete", index))
    frozen_pairs = {
        (row["source_layer"], int(row["source_item_index"])) for row in membership
    }
    if require_exact_valid_output_membership:
        if reconstructed_members != frozen_pairs:
            raise RuntimeError("membership does not exactly match valid source outputs")
    elif not frozen_pairs.issubset(reconstructed_members):
        raise RuntimeError("corrected membership is not a subset of valid source outputs")
    if not require_exact_valid_output_membership and len(
        {row["title_sha256"] for row in membership}
    ) != len(membership):
        raise RuntimeError("corrected membership contains repeated final title text")

    override_by_source_hash: dict[str, dict[str, str]] = {}
    if title_overrides_path is not None:
        override_rows = read_csv(title_overrides_path)
        override_by_source_hash = {
            row["source_title_sha256"]: row for row in override_rows
        }
        if len(override_by_source_hash) != len(override_rows):
            raise RuntimeError("duplicate source hash in title override file")

    units: list[Unit] = []
    for member in membership:
        source_layer = member["source_layer"]
        source_index = int(member["source_item_index"])
        if source_layer == "frozen_v0_3_5000":
            result = frozen_results[source_index]
            meta = frozen_meta[source_index]
            source_record_id = meta["record_id"]
            sampling_track = meta["sampling_track"]
        elif source_layer == "run70_complete":
            result = run70_results[source_index]
            meta = run70_meta[source_index]
            source_record_id = result["record_id"]
            sampling_track = "probability"
        else:
            raise RuntimeError(f"unknown source layer: {source_layer}")
        if member["record_key_sha256"] != record_key(source_record_id):
            raise RuntimeError("record key mismatch in paper-corpus membership")
        source_title_sha256 = member.get("source_title_sha256") or member["title_sha256"]
        if source_title_sha256 != meta["title_sha256"]:
            raise RuntimeError("source title hash mismatch in paper-corpus membership")
        title = meta.get("deidentified_title")
        if not isinstance(title, str) or not title:
            raise RuntimeError("missing deidentified title required for C4 sensitivity")
        current_title_sha256 = member["title_sha256"]
        if current_title_sha256 != source_title_sha256:
            override = override_by_source_hash.get(source_title_sha256)
            if override is None:
                raise RuntimeError("missing required privacy-remediation title override")
            if int(override["corpus_index"]) != int(member["corpus_index"]):
                raise RuntimeError("title override corpus index mismatch")
            if override["title_sha256"] != current_title_sha256:
                raise RuntimeError("title override hash differs from membership")
            title = override["remediated_title"]
            if hashlib.sha256(title.encode("utf-8")).hexdigest() != current_title_sha256:
                raise RuntimeError("remediated title content hash mismatch")
        elif source_title_sha256 in override_by_source_hash:
            raise RuntimeError("unapplied title override declared for unchanged member")
        units.append(
            Unit(
                corpus_index=int(member["corpus_index"]),
                source_layer=source_layer,
                sampling_track=sampling_track,
                source_site=meta["source_site"],
                length_band=meta["length_band"],
                title=title,
                title_sha256=member["title_sha256"],
                output=result["output"],
                derived=result.get("derived") or {},
                normalization_actions=tuple(result.get("normalization_actions") or ()),
            )
        )
    return units, freeze


def privacy_relevant(unit: Unit) -> bool:
    return unit.output.get("privacy_chain_relevance") == "relevant"


def distribution_visible(unit: Unit) -> bool:
    return privacy_relevant(unit) and unit.output.get("distribution_visibility") == "visible"


def position_visible(value: Any) -> bool:
    return value in VISIBLE_POSITIONS


def strict_feminine_distribution(unit: Unit) -> bool:
    return distribution_visible(unit) and unit.output.get("distribution_target_position") == "feminine"


def any_feminine_person_position(unit: Unit) -> bool:
    return any(unit.output.get(field) == "feminine" for field in PERSON_POSITION_FIELDS)


def metric_specs() -> list[MetricSpec]:
    def stage_opportunity(visibility: tuple[str, ...], positions: tuple[str, ...]) -> Callable[[Unit], bool]:
        return lambda unit: privacy_relevant(unit) and all(
            unit.output.get(field) == "visible" for field in visibility
        ) and all(position_visible(unit.output.get(field)) for field in positions)

    def stage_same(positions: tuple[str, ...]) -> Callable[[Unit], bool]:
        return lambda unit: len({unit.output.get(field) for field in positions}) == 1

    creation_distribution_visibility = (
        "material_creation_visibility",
        "distribution_visibility",
    )
    creation_distribution_positions = (
        "material_creation_target_position",
        "distribution_target_position",
    )
    distribution_exposure_visibility = (
        "distribution_visibility",
        "exposure_visibility",
    )
    distribution_exposure_positions = (
        "distribution_target_position",
        "exposure_target_position",
    )
    three_stage_visibility = (
        "material_creation_visibility",
        "distribution_visibility",
        "exposure_visibility",
    )
    three_stage_positions = (
        "material_creation_target_position",
        "distribution_target_position",
        "exposure_target_position",
    )
    return [
        MetricSpec(
            "C1",
            "strict_feminine_distribution_target",
            "proportion",
            distribution_visible,
            lambda unit: unit.output.get("distribution_target_position") == "feminine",
            direction="greater_than_0.5",
        ),
        MetricSpec(
            "C1",
            "feminine_or_mixed_distribution_target",
            "proportion",
            distribution_visible,
            lambda unit: unit.output.get("distribution_target_position")
            in {"feminine", "mixed_multiple"},
        ),
        MetricSpec(
            "B1",
            "distribution_target_position_visible",
            "proportion",
            distribution_visible,
            lambda unit: position_visible(
                unit.output.get("distribution_target_position")
            ),
        ),
        MetricSpec(
            "B1",
            "distribution_actor_position_visible",
            "proportion",
            distribution_visible,
            lambda unit: position_visible(
                unit.output.get("distribution_actor_position")
            ),
        ),
        MetricSpec(
            "B1",
            "target_minus_actor_visibility_paired_difference",
            "paired_difference",
            distribution_visible,
            lambda unit: position_visible(unit.output.get("distribution_target_position")),
            lambda unit: position_visible(unit.output.get("distribution_actor_position")),
            direction="greater_than_0",
        ),
        MetricSpec(
            "C2",
            "creation_to_distribution_target_position_consistency",
            "proportion",
            stage_opportunity(creation_distribution_visibility, creation_distribution_positions),
            stage_same(creation_distribution_positions),
            direction="greater_than_0.5",
        ),
        MetricSpec(
            "C2",
            "distribution_to_exposure_target_position_consistency",
            "proportion",
            stage_opportunity(distribution_exposure_visibility, distribution_exposure_positions),
            stage_same(distribution_exposure_positions),
            direction="greater_than_0.5",
        ),
        MetricSpec(
            "C2",
            "three_stage_target_position_consistency",
            "proportion",
            stage_opportunity(three_stage_visibility, three_stage_positions),
            stage_same(three_stage_positions),
            direction="greater_than_0.5",
        ),
        MetricSpec(
            "C4",
            "no_visible_authorization_language_frozen_v03",
            "proportion",
            distribution_visible,
            lambda unit: unit.output.get("distribution_authorization_visibility")
            == "no_visible_authorization_language",
        ),
        MetricSpec(
            "C5",
            "feminine_objectification",
            "proportion",
            strict_feminine_distribution,
            lambda unit: unit.output.get("objectification_target_position") == "feminine",
        ),
        MetricSpec(
            "C5",
            "any_feminine_person_position",
            "proportion",
            strict_feminine_distribution,
            any_feminine_person_position,
        ),
        MetricSpec(
            "C5",
            "objectification_and_all_four_person_positions_absent",
            "proportion",
            strict_feminine_distribution,
            lambda unit: unit.output.get("objectification_target_position") == "feminine"
            and not any_feminine_person_position(unit),
        ),
        MetricSpec(
            "C5",
            "objectification_minus_any_person_position_paired_difference",
            "paired_difference",
            strict_feminine_distribution,
            lambda unit: unit.output.get("objectification_target_position") == "feminine",
            any_feminine_person_position,
            direction="greater_than_0",
        ),
    ]


def metric_dictionary() -> list[dict[str, str]]:
    rows = [
        (
            "C1",
            "strict_feminine_distribution_target",
            "distribution_visibility=visible within privacy_chain_relevance=relevant",
            "distribution_target_position equals feminine",
            "supporting_result",
        ),
        (
            "C1",
            "feminine_or_mixed_distribution_target",
            "distribution_visibility=visible within privacy_chain_relevance=relevant",
            "distribution_target_position is feminine or mixed_multiple",
            "supporting_result_continuous_no_75pct_gate",
        ),
        (
            "B1",
            "distribution_target_position_visible",
            "same 4,224 visible-distribution opportunities as C1",
            "distribution target has any contract-defined visible position",
            "main_result_component",
        ),
        (
            "B1",
            "distribution_actor_position_visible",
            "same 4,224 visible-distribution opportunities as C1",
            "distribution actor has any contract-defined visible position",
            "main_result_component",
        ),
        (
            "B1",
            "target_minus_actor_visibility_paired_difference",
            "same 4,224 visible-distribution opportunities as C1",
            "within-title target-visible rate minus actor-visible rate; discordant pairs drive the difference",
            "main_result",
        ),
        (
            "C2",
            "creation_to_distribution_target_position_consistency",
            "creation and distribution both visible and both target positions visible",
            "exact equality of the two contract position labels",
            "main_result",
        ),
        (
            "C2",
            "distribution_to_exposure_target_position_consistency",
            "distribution and exposure both visible and both target positions visible",
            "exact equality of the two contract position labels",
            "main_result",
        ),
        (
            "C2",
            "three_stage_target_position_consistency",
            "creation, distribution and exposure all visible with all target positions visible",
            "exact equality of all three contract position labels",
            "main_result",
        ),
        (
            "C4",
            "no_visible_authorization_language_frozen_v03",
            "same 4,224 visible-distribution opportunities as C1",
            "frozen distribution_authorization_visibility equals no_visible_authorization_language",
            "definition_sensitivity_only",
        ),
        (
            "C5",
            "feminine_objectification",
            "distribution target is strictly feminine in a visible distribution event",
            "objectification_target_position equals feminine",
            "supporting_result_component",
        ),
        (
            "C5",
            "any_feminine_person_position",
            "same strictly feminine distribution-target opportunities",
            "feminine is visible in any desire, pleasure, refusal/resistance or attributed-speech position",
            "supporting_result_component",
        ),
        (
            "C5",
            "objectification_and_all_four_person_positions_absent",
            "same strictly feminine distribution-target opportunities",
            "feminine objectification visible while all four person-position fields lack feminine",
            "supporting_result",
        ),
        (
            "C5",
            "objectification_minus_any_person_position_paired_difference",
            "same strictly feminine distribution-target opportunities",
            "within-title objectification rate minus any-person-position rate",
            "supporting_result_direct_contrast",
        ),
    ]
    return [
        {
            "candidate": candidate,
            "metric": metric,
            "opportunity_denominator_definition": opportunity,
            "numerator_or_contrast_definition": definition,
            "manuscript_role": role,
        }
        for candidate, metric, opportunity, definition, role in rows
    ]


def estimate_metric(spec: MetricSpec, units: Sequence[Unit]) -> dict[str, Any]:
    opportunity = [unit for unit in units if spec.opportunity(unit)]
    if spec.kind == "proportion":
        selected = sum(spec.selected(unit) for unit in opportunity)
        denominator = len(opportunity)
        estimate = selected / denominator if denominator else math.nan
        lower, upper = wilson_interval(selected, denominator)
        return {
            "candidate": spec.candidate,
            "metric": spec.metric,
            "estimate_type": "corpus_proportion",
            "selected_n": selected,
            "comparison_selected_n": "",
            "both_n": "",
            "neither_n": "",
            "denominator_n": denominator,
            "estimate": estimate,
            "ci95_low": lower,
            "ci95_high": upper,
            "interval_method": "wilson_score_95_superpopulation_heuristic",
            "mcnemar_exact_p": "",
            "mcnemar_exact_log10_p": "",
            "direction_rule": spec.direction,
        }
    if spec.kind == "paired_difference" and spec.comparison is not None:
        paired = paired_difference(
            [spec.selected(unit) for unit in opportunity],
            [spec.comparison(unit) for unit in opportunity],
        )
        return {
            "candidate": spec.candidate,
            "metric": spec.metric,
            "estimate_type": "paired_risk_difference",
            **paired,
            "interval_method": "paired_difference_normal_95",
            "direction_rule": spec.direction,
        }
    raise ValueError(f"unsupported metric kind: {spec.kind}")


def direction_retained(result: dict[str, Any]) -> bool | str:
    rule = result["direction_rule"]
    estimate = float(result["estimate"])
    if not math.isfinite(estimate) or result["denominator_n"] == 0:
        return False
    if rule == "greater_than_0":
        return estimate > 0
    if rule == "greater_than_0.5":
        return estimate > 0.5
    return "not_applicable"


def corpus_descriptives(units: Sequence[Unit]) -> list[dict[str, Any]]:
    specs: list[tuple[str, Callable[[Unit], bool], str]] = [
        ("paper_corpus_records", lambda _unit: True, "all frozen valid records"),
        ("frozen_v0_3_5000_layer", lambda unit: unit.source_layer == "frozen_v0_3_5000", "source layer"),
        ("run70_complete_layer", lambda unit: unit.source_layer == "run70_complete", "source layer"),
        ("probability_track_records", lambda unit: unit.sampling_track == "probability", "sampling composition; not a population weight"),
        ("mechanism_track_records", lambda unit: unit.sampling_track == "mechanism", "sampling composition; enriched track"),
        ("privacy_chain_relevant", privacy_relevant, "privacy-chain coding opportunity"),
        ("distribution_visible", distribution_visible, "distribution-stage opportunity"),
        ("strict_feminine_distribution_target", strict_feminine_distribution, "C5 opportunity"),
        ("needs_adjudication", lambda unit: bool(unit.output.get("needs_adjudication")), "quality flag"),
        ("evidence_complete", lambda unit: bool(unit.derived.get("evidence_complete")), "quality diagnostic"),
        ("normalization_action_present", lambda unit: bool(unit.normalization_actions), "deterministic representation repair, not manual relabeling"),
    ]
    denominator = len(units)
    return [
        {
            "statistic": name,
            "count": (count := sum(predicate(unit) for unit in units)),
            "denominator_n": denominator,
            "proportion_of_corpus": count / denominator,
            "role": role,
        }
        for name, predicate, role in specs
    ]


def position_distributions(units: Sequence[Unit]) -> list[dict[str, Any]]:
    contexts = [
        ("distribution_visible", "distribution_target_position", distribution_visible),
        ("distribution_visible", "distribution_actor_position", distribution_visible),
        (
            "material_creation_visible",
            "material_creation_target_position",
            lambda unit: privacy_relevant(unit)
            and unit.output.get("material_creation_visibility") == "visible",
        ),
        (
            "exposure_visible",
            "exposure_target_position",
            lambda unit: privacy_relevant(unit)
            and unit.output.get("exposure_visibility") == "visible",
        ),
        ("strict_feminine_distribution", "objectification_target_position", strict_feminine_distribution),
        *[
            ("strict_feminine_distribution", field, strict_feminine_distribution)
            for field in PERSON_POSITION_FIELDS
        ],
    ]
    rows: list[dict[str, Any]] = []
    for context, field, opportunity in contexts:
        subset = [unit for unit in units if opportunity(unit)]
        counts = Counter(str(unit.output.get(field)) for unit in subset)
        for value, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
            lower, upper = wilson_interval(count, len(subset))
            rows.append(
                {
                    "context": context,
                    "field": field,
                    "value": value,
                    "count": count,
                    "denominator_n": len(subset),
                    "proportion": count / len(subset) if subset else math.nan,
                    "wilson_95_low": lower,
                    "wilson_95_high": upper,
                }
            )
    return rows


def analysis_scopes(units: Sequence[Unit]) -> dict[str, list[Unit]]:
    return {
        "all_frozen_corpus": list(units),
        "probability_tracks_only": [unit for unit in units if unit.sampling_track == "probability"],
        "run70_probability_only": [unit for unit in units if unit.source_layer == "run70_complete"],
        "frozen5000_probability_only": [
            unit
            for unit in units
            if unit.source_layer == "frozen_v0_3_5000" and unit.sampling_track == "probability"
        ],
        "frozen5000_mechanism_only": [
            unit
            for unit in units
            if unit.source_layer == "frozen_v0_3_5000" and unit.sampling_track == "mechanism"
        ],
    }


def sensitivity_table(
    scopes: dict[str, Sequence[Unit]], specs: Sequence[MetricSpec]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for scope, units in scopes.items():
        for spec in specs:
            result = estimate_metric(spec, units)
            rows.append(
                {
                    "scope": scope,
                    "scope_records": len(units),
                    "candidate": spec.candidate,
                    "metric": spec.metric,
                    "selected_n": result["selected_n"],
                    "comparison_selected_n": result["comparison_selected_n"],
                    "denominator_n": result["denominator_n"],
                    "estimate": result["estimate"],
                    "ci95_low": result["ci95_low"],
                    "ci95_high": result["ci95_high"],
                    "direction_rule": result["direction_rule"],
                    "direction_retained": direction_retained(result),
                }
            )
    return rows


def quality_scopes(units: Sequence[Unit]) -> dict[str, list[Unit]]:
    return {
        "all_frozen_corpus": list(units),
        "no_adjudication_flag": [
            unit for unit in units if not unit.output.get("needs_adjudication")
        ],
        "evidence_complete": [
            unit for unit in units if unit.derived.get("evidence_complete") is True
        ],
        "no_normalization_action": [
            unit for unit in units if not unit.normalization_actions
        ],
    }


def leave_one_group_out(
    units: Sequence[Unit], specs: Sequence[MetricSpec]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for dimension in ("source_site", "length_band"):
        groups = sorted({getattr(unit, dimension) for unit in units})
        for group in groups:
            subset = [unit for unit in units if getattr(unit, dimension) != group]
            for spec in specs:
                result = estimate_metric(spec, subset)
                rows.append(
                    {
                        "dimension": dimension,
                        "deleted_group_sha256_16": group_hash(group),
                        "remaining_corpus_n": len(subset),
                        "candidate": spec.candidate,
                        "metric": spec.metric,
                        "denominator_n": result["denominator_n"],
                        "estimate": result["estimate"],
                        "direction_rule": result["direction_rule"],
                        "direction_retained": direction_retained(result),
                    }
                )
    return rows


def sensitivity_ranges(
    sampling: Sequence[dict[str, Any]],
    quality: Sequence[dict[str, Any]],
    deletions: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    families: list[tuple[str, Sequence[dict[str, Any]]]] = [
        ("sampling_composition_scope", sampling),
        ("quality_scope", quality),
        (
            "leave_one_source_site_out",
            [row for row in deletions if row["dimension"] == "source_site"],
        ),
        (
            "leave_one_length_band_out",
            [row for row in deletions if row["dimension"] == "length_band"],
        ),
    ]
    rows: list[dict[str, Any]] = []
    for family, family_rows in families:
        keys = sorted({(row["candidate"], row["metric"]) for row in family_rows})
        for candidate, metric in keys:
            subset = [
                row
                for row in family_rows
                if row["candidate"] == candidate and row["metric"] == metric
            ]
            estimates = [float(row["estimate"]) for row in subset]
            denominators = [int(row["denominator_n"]) for row in subset]
            relevant_direction_rows = [
                row
                for row in subset
                if row["direction_retained"] != "not_applicable"
            ]
            rows.append(
                {
                    "sensitivity_family": family,
                    "candidate": candidate,
                    "metric": metric,
                    "variant_count": len(subset),
                    "minimum_denominator_n": min(denominators),
                    "maximum_denominator_n": max(denominators),
                    "minimum_estimate": min(estimates),
                    "maximum_estimate": max(estimates),
                    "direction_evaluated_variants": len(relevant_direction_rows),
                    "direction_failure_count": sum(
                        row["direction_retained"] is not True
                        for row in relevant_direction_rows
                    ),
                }
            )
    return rows


def authorization_boundary_sensitivity(units: Sequence[Unit]) -> list[dict[str, Any]]:
    config = json.loads(AUTHORIZATION_CONFIG.read_text(encoding="utf-8"))
    opportunity = [unit for unit in units if distribution_visible(unit)]
    rows: list[dict[str, Any]] = []
    frozen_no_visible = sum(
        unit.output.get("distribution_authorization_visibility")
        == "no_visible_authorization_language"
        for unit in opportunity
    )
    frozen_unauthorized = sum(
        unit.output.get("distribution_authorization_visibility") == "unauthorized_explicit"
        for unit in opportunity
    )
    frozen_authorized = sum(
        unit.output.get("distribution_authorization_visibility") == "authorized_explicit"
        for unit in opportunity
    )
    frozen_unclear = sum(
        unit.output.get("distribution_authorization_visibility") == "unclear"
        for unit in opportunity
    )
    for boundary in config["boundaries"]:
        pattern = re.compile(boundary["regex"]) if boundary["regex"] else None
        reclassified = (
            sum(
                unit.output.get("distribution_authorization_visibility")
                == "no_visible_authorization_language"
                and pattern.search(unit.title) is not None
                for unit in opportunity
            )
            if pattern is not None
            else 0
        )
        no_visible = frozen_no_visible - reclassified
        unauthorized = frozen_unauthorized + reclassified
        lower, upper = wilson_interval(no_visible, len(opportunity))
        rows.append(
            {
                "boundary": boundary["id"],
                "distribution_opportunity_n": len(opportunity),
                "reclassified_from_no_visible_to_unauthorized_n": reclassified,
                "no_visible_authorization_n": no_visible,
                "no_visible_authorization_rate": no_visible / len(opportunity),
                "no_visible_wilson_95_low": lower,
                "no_visible_wilson_95_high": upper,
                "unauthorized_explicit_n": unauthorized,
                "unauthorized_explicit_rate": unauthorized / len(opportunity),
                "authorized_explicit_n": frozen_authorized,
                "authorized_explicit_rate": frozen_authorized / len(opportunity),
                "unclear_n": frozen_unclear,
                "unclear_rate": frozen_unclear / len(opportunity),
                "interpretation": "deterministic lexical boundary sensitivity; no frozen label overwritten",
            }
        )
    return rows


def transition_tables(units: Sequence[Unit]) -> list[dict[str, Any]]:
    pairs = [
        (
            "creation_to_distribution",
            ("material_creation_visibility", "distribution_visibility"),
            ("material_creation_target_position", "distribution_target_position"),
        ),
        (
            "distribution_to_exposure",
            ("distribution_visibility", "exposure_visibility"),
            ("distribution_target_position", "exposure_target_position"),
        ),
    ]
    rows: list[dict[str, Any]] = []
    for transition, visibility, positions in pairs:
        subset = [
            unit
            for unit in units
            if privacy_relevant(unit)
            and all(unit.output.get(field) == "visible" for field in visibility)
            and all(position_visible(unit.output.get(field)) for field in positions)
        ]
        counts = Counter(
            (unit.output.get(positions[0]), unit.output.get(positions[1]))
            for unit in subset
        )
        for (origin, destination), count in sorted(
            counts.items(), key=lambda item: (-item[1], item[0])
        ):
            rows.append(
                {
                    "transition": transition,
                    "origin_position": origin,
                    "destination_position": destination,
                    "count": count,
                    "denominator_n": len(subset),
                    "proportion": count / len(subset) if subset else math.nan,
                }
            )
    return rows


def render_report(
    metrics: Sequence[dict[str, Any]],
    descriptives: Sequence[dict[str, Any]],
    authorization: Sequence[dict[str, Any]],
    sampling: Sequence[dict[str, Any]],
    quality: Sequence[dict[str, Any]],
    deletions: Sequence[dict[str, Any]],
    ranges: Sequence[dict[str, Any]],
    freeze: dict[str, Any],
) -> str:
    by_metric = {(row["candidate"], row["metric"]): row for row in metrics}
    desc = {row["statistic"]: row for row in descriptives}
    auth = {row["boundary"]: row for row in authorization}

    def pct(value: Any) -> str:
        return f"{100 * float(value):.2f}%"

    def p_text(row: dict[str, Any]) -> str:
        value = float(row["mcnemar_exact_p"])
        if value == 0.0:
            return f"p < 10^{math.floor(float(row['mcnemar_exact_log10_p'])) + 1}"
        return f"p = {value:.3g}"

    def sensitivity_range(
        family: str, candidate: str, metric: str
    ) -> tuple[str, str]:
        row = next(
            item
            for item in ranges
            if item["sensitivity_family"] == family
            and item["candidate"] == candidate
            and item["metric"] == metric
        )
        return pct(row["minimum_estimate"]), pct(row["maximum_estimate"])

    c1s = by_metric[("C1", "strict_feminine_distribution_target")]
    c1b = by_metric[("C1", "feminine_or_mixed_distribution_target")]
    b1t = by_metric[("B1", "distribution_target_position_visible")]
    b1a = by_metric[("B1", "distribution_actor_position_visible")]
    b1 = by_metric[("B1", "target_minus_actor_visibility_paired_difference")]
    c2a = by_metric[("C2", "creation_to_distribution_target_position_consistency")]
    c2b = by_metric[("C2", "distribution_to_exposure_target_position_consistency")]
    c2c = by_metric[("C2", "three_stage_target_position_consistency")]
    c5o = by_metric[("C5", "feminine_objectification")]
    c5p = by_metric[("C5", "any_feminine_person_position")]
    c5j = by_metric[("C5", "objectification_and_all_four_person_positions_absent")]
    c5d = by_metric[("C5", "objectification_minus_any_person_position_paired_difference")]

    core_keys = {
        ("B1", "target_minus_actor_visibility_paired_difference"),
        ("C2", "creation_to_distribution_target_position_consistency"),
        ("C2", "distribution_to_exposure_target_position_consistency"),
        ("C2", "three_stage_target_position_consistency"),
        ("C5", "objectification_minus_any_person_position_paired_difference"),
    }
    core_deletions = [
        row for row in deletions if (row["candidate"], row["metric"]) in core_keys
    ]
    deletion_failures = sum(row["direction_retained"] is not True for row in core_deletions)
    scope_core = [
        row for row in sampling if (row["candidate"], row["metric"]) in core_keys
    ]
    scope_failures = sum(row["direction_retained"] is not True for row in scope_core)
    quality_core = [
        row for row in quality if (row["candidate"], row["metric"]) in core_keys
    ]
    quality_failures = sum(row["direction_retained"] is not True for row in quality_core)
    b1_source_range = sensitivity_range(
        "leave_one_source_site_out",
        "B1",
        "target_minus_actor_visibility_paired_difference",
    )
    b1_length_range = sensitivity_range(
        "leave_one_length_band_out",
        "B1",
        "target_minus_actor_visibility_paired_difference",
    )
    c1b_source_range = sensitivity_range(
        "leave_one_source_site_out", "C1", "feminine_or_mixed_distribution_target"
    )
    c1b_length_range = sensitivity_range(
        "leave_one_length_band_out", "C1", "feminine_or_mixed_distribution_target"
    )
    c5_source_range = sensitivity_range(
        "leave_one_source_site_out",
        "C5",
        "objectification_minus_any_person_position_paired_difference",
    )
    c5_length_range = sensitivity_range(
        "leave_one_length_band_out",
        "C5",
        "objectification_minus_any_person_position_paired_difference",
    )

    source_frame_n = int(
        freeze["scope"].get(
            "source_frame_unique_final_texts",
            freeze["scope"].get("source_frame_records_before_paper_freeze"),
        )
    )
    source_frame_unit = (
        "unique-title"
        if "unique" in str(freeze["scope"].get("unit", "")).lower()
        else "title-record"
    )

    return f"""# Frozen manuscript corpus analysis

## Scope and result

The analysis verified and used exactly **{desc['paper_corpus_records']['count']:,}**
members of `{freeze['freeze_id']}`. All retained units are represented by contract-valid
v0.3 outputs with `record_validity=valid`. The analysis made **0 API calls**, did
not read `data/raw/`, and did not rewrite frozen annotations.

The corpus contains {desc['privacy_chain_relevant']['count']:,} privacy-chain-
relevant titles, of which {desc['distribution_visible']['count']:,} provide the
formal distribution-stage opportunity used for B1, C1 and C4. These are
descriptions of the frozen manuscript corpus, not estimates for the earlier
{source_frame_n:,}-{source_frame_unit} frame or for all Chinese adult-film titles.

## Main candidate results

- **B1 (main result):** target position visibility was
  **{pct(b1t['estimate'])}** ({b1t['selected_n']:,}/{b1t['denominator_n']:,}),
  while actor position visibility was **{pct(b1a['estimate'])}**
  ({b1a['selected_n']:,}/{b1a['denominator_n']:,}). Target visibility exceeded
  actor visibility by **{pct(b1['estimate'])}**
  in {b1['denominator_n']:,} distribution opportunities. Discordant pairs were
  {b1['selected_n']:,} target-visible/actor-outside-visible-set versus
  {b1['comparison_selected_n']:,} actor-visible/target-outside-visible-set
  (95% paired interval {pct(b1['ci95_low'])}–{pct(b1['ci95_high'])}; exact
  McNemar {p_text(b1)}).
- **C2 (main result):** exact target-position consistency was
  **{pct(c2a['estimate'])}** from
  creation to distribution ({c2a['selected_n']:,}/{c2a['denominator_n']:,}),
  **{pct(c2b['estimate'])}** from distribution to exposure
  ({c2b['selected_n']:,}/{c2b['denominator_n']:,}), and
  **{pct(c2c['estimate'])}** across all three stages
  ({c2c['selected_n']:,}/{c2c['denominator_n']:,}).
- **C1 (supporting result):** among distribution opportunities, explicitly feminine targets were
  **{pct(c1s['estimate'])}** ({c1s['selected_n']:,}/{c1s['denominator_n']:,});
  feminine or mixed-multiple targets were **{pct(c1b['estimate'])}**
  ({c1b['selected_n']:,}/{c1b['denominator_n']:,}). The second figure is
  reported continuously, not judged against the retired 75% project gate.
- **C5 (supporting direct contrast):** among {c5o['denominator_n']:,} strictly feminine distribution targets,
  feminine objectification was visible in **{pct(c5o['estimate'])}**, whereas
  any feminine desire, pleasure, refusal/resistance or attributed-speech
  position was visible in **{pct(c5p['estimate'])}**. Their paired difference
  was **{pct(c5d['estimate'])}** (95% paired interval
  {pct(c5d['ci95_low'])}–{pct(c5d['ci95_high'])}; exact McNemar
  {p_text(c5d)});
  objectification with all four person positions absent occurred in
  **{pct(c5j['estimate'])}**.
- **C4 remains definition-sensitive:** frozen v0.3 coding gives
  **{pct(auth['frozen_v03']['no_visible_authorization_rate'])}** with no visible
  authorization language; the narrow lexical boundary gives
  **{pct(auth['narrow_explicit_victim_or_stealth']['no_visible_authorization_rate'])}**,
  and the broad leakage boundary gives
  **{pct(auth['codex_broad_leakage_semantics']['no_visible_authorization_rate'])}**.
  It must not be promoted to a stable claim or interpreted as actual consent.

### Manuscript-use decision

| Candidate | Formal opportunity denominator | Manuscript use |
|---|---:|---|
| B1 | {b1['denominator_n']:,} titles with a visible distribution event | **Primary result**: paired target-versus-actor position visibility |
| C2 creation→distribution | {c2a['denominator_n']:,} titles with both stages and positions visible | **Primary result**: exact label continuity |
| C2 distribution→exposure | {c2b['denominator_n']:,} titles with both stages and positions visible | **Primary result**: exact label continuity |
| C2 three stages | {c2c['denominator_n']:,} titles with all stages and positions visible | **Primary result**: exact label continuity |
| C1 | {c1s['denominator_n']:,} visible-distribution titles | Supporting composition result; report strict and broad definitions continuously |
| C5 | {c5o['denominator_n']:,} strictly feminine distribution targets | Supporting within-title contrast; avoid treating four fields as exhaustive subjectivity |
| C4 | {b1['denominator_n']:,} visible-distribution titles | Sensitivity/limitation only; not a stable substantive finding |

“Continuity” means equality of contract position labels within one title. It does
not establish that the stages concern the same real person or event.

## Robustness and quality

- Core directional failures across probability/mechanism composition scopes:
  **{scope_failures}/{len(scope_core)}**.
- Core directional failures across all-record, no-adjudication,
  evidence-complete and no-normalization subsets: **{quality_failures}/{len(quality_core)}**.
- Core directional failures after each source-site or length-band deletion:
  **{deletion_failures}/{len(core_deletions)}**.

Selected ranges make the scale of heterogeneity explicit:

- B1 paired difference: leave-one-source {b1_source_range[0]}–{b1_source_range[1]};
  leave-one-length-band {b1_length_range[0]}–{b1_length_range[1]}.
- C1 feminine-or-mixed rate: leave-one-source
  {c1b_source_range[0]}–{c1b_source_range[1]}; leave-one-length-band
  {c1b_length_range[0]}–{c1b_length_range[1]}. Falling below the retired 75%
  gate in a deletion variant is retained as heterogeneity, not treated as a
  binary failure.
- C5 objectification-minus-person-position difference: leave-one-source
  {c5_source_range[0]}–{c5_source_range[1]}; leave-one-length-band
  {c5_length_range[0]}–{c5_length_range[1]}.

The full `sampling_design_sensitivity.csv` separates the run-70 probability
layer, the frozen probability layer and the enriched mechanism layer. The full
`quality_sensitivity.csv` reports all metrics after excluding adjudication-
flagged records, restricting to evidence-complete records, and excluding any
record with deterministic normalization actions. `sensitivity_ranges.csv`
condenses all source, length, quality and composition variants.

The intervals in the tables and the paired exact tests are superpopulation
heuristics. Because all {desc['paper_corpus_records']['count']:,} retained units were analyzed, they are not
sampling-error statements for the frozen corpus itself. The frozen corpus was
partly budget- and design-defined; neither small p-values nor robustness across
its component tracks authorizes generalization beyond it.
"""


def analyze(
    output_dir: Path,
    *,
    freeze_manifest_path: Path = FREEZE_MANIFEST,
    membership_path: Path = MEMBERSHIP,
    expected_corpus_n: int = EXPECTED_CORPUS_N,
    analysis_id: str = "gendered_visibility_paper_corpus_v1_analysis_v1",
    require_exact_valid_output_membership: bool = True,
    title_overrides_path: Path | None = None,
    script_path: Path = SCRIPT,
) -> dict[str, Any]:
    units, freeze = load_units(
        freeze_manifest_path,
        membership_path,
        expected_corpus_n,
        require_exact_valid_output_membership=require_exact_valid_output_membership,
        title_overrides_path=title_overrides_path,
    )
    specs = metric_specs()
    definitions = metric_dictionary()
    descriptives = corpus_descriptives(units)
    metrics = [estimate_metric(spec, units) for spec in specs]
    positions = position_distributions(units)
    sampling = sensitivity_table(analysis_scopes(units), specs)
    quality = sensitivity_table(quality_scopes(units), specs)
    deletions = leave_one_group_out(units, specs)
    ranges = sensitivity_ranges(sampling, quality, deletions)
    authorization = authorization_boundary_sensitivity(units)
    transitions = transition_tables(units)

    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, Sequence[dict[str, Any]]] = {
        "metric_dictionary.csv": definitions,
        "corpus_descriptives.csv": descriptives,
        "candidate_metrics.csv": metrics,
        "position_distributions.csv": positions,
        "sampling_design_sensitivity.csv": sampling,
        "quality_sensitivity.csv": quality,
        "leave_one_group_out.csv": deletions,
        "sensitivity_ranges.csv": ranges,
        "authorization_boundary_sensitivity.csv": authorization,
        "stage_transition_matrix.csv": transitions,
    }
    for filename, rows in outputs.items():
        write_csv(output_dir / filename, rows)

    report = render_report(
        metrics,
        descriptives,
        authorization,
        sampling,
        quality,
        deletions,
        ranges,
        freeze,
    )
    report_path = output_dir / "report.md"
    report_path.write_text(report, encoding="utf-8")

    resolved_override_path = resolve_title_overrides_path(freeze, freeze_manifest_path)
    input_paths = (
        freeze_manifest_path,
        membership_path,
        FROZEN_RECORDS,
        FROZEN_SAMPLE,
        RUN70_RECORDS,
        RUN70_FRAME,
        AUTHORIZATION_CONFIG,
        script_path,
    ) + ((resolved_override_path,) if resolved_override_path is not None else ())
    output_paths = [output_dir / filename for filename in outputs] + [report_path]
    manifest = {
        "created_at": utc_now(),
        "analysis_id": analysis_id,
        "status": "complete_offline_corpus_descriptive_analysis",
        "freeze_id": freeze["freeze_id"],
        "corpus_records": len(units),
        "selection_rule": freeze["selection_rule"],
        "estimand": (
            "unweighted descriptions of the complete corrected manuscript corpus; "
            f"unit={freeze['scope'].get('unit', 'record-key-unique title record')}"
        ),
        "inference_boundary": freeze["scope"]["inference_boundary"],
        "statistical_uncertainty_note": (
            "Wilson and paired large-sample 95% intervals plus exact McNemar "
            "tests are superpopulation heuristics, not finite-corpus sampling-"
            "error statements or authorization to generalize beyond the corpus."
        ),
        "privacy": (
            "Public outputs are aggregate-only. Source-site and length-band "
            "deletion groups are hashed; titles, record IDs, URLs and evidence "
            "spans are not emitted."
        ),
        "inputs": {
            path.relative_to(ROOT).as_posix(): sha256_file(path) for path in input_paths
        },
        "outputs": {
            path.relative_to(ROOT).as_posix(): sha256_file(path) for path in output_paths
        },
        "api_calls": 0,
        "raw_data_files_read": 0,
        "frozen_outputs_rewritten": 0,
    }
    manifest_path = output_dir / "manifest.json"
    write_json(manifest_path, manifest)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--freeze-manifest", type=Path, default=FREEZE_MANIFEST)
    parser.add_argument("--membership", type=Path, default=MEMBERSHIP)
    parser.add_argument("--title-overrides", type=Path)
    parser.add_argument("--expected-corpus-n", type=int, default=EXPECTED_CORPUS_N)
    parser.add_argument(
        "--analysis-id",
        default="gendered_visibility_paper_corpus_v1_analysis_v1",
    )
    parser.add_argument(
        "--allow-valid-output-subset",
        action="store_true",
        help="Allow a checksum-verified unique-text subset of valid source outputs.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = analyze(
        args.output_dir.resolve(),
        freeze_manifest_path=args.freeze_manifest.resolve(),
        membership_path=args.membership.resolve(),
        expected_corpus_n=args.expected_corpus_n,
        analysis_id=args.analysis_id,
        require_exact_valid_output_membership=not args.allow_valid_output_subset,
        title_overrides_path=(
            args.title_overrides.resolve() if args.title_overrides else None
        ),
    )
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
