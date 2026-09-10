"""Create a deterministic stratified deidentification pilot for review.

The pilot applies every current candidate span found in the sampled rows. This is
an internal review artifact, not an approved release dataset. Full-corpus
decisions remain untouched until the pilot is accepted by a human reviewer.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path

if __package__:
    from src.preprocessing.apply_deidentification import (
        POLITICAL_EXCLUSION_SIGNAL,
        apply_span_replacements,
        political_exclusion_record_ids,
        sha256_file,
    )
else:
    from apply_deidentification import (
        POLITICAL_EXCLUSION_SIGNAL,
        apply_span_replacements,
        political_exclusion_record_ids,
        sha256_file,
    )

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = PROJECT_ROOT / "data" / "interim" / "10_combined_deduplicated" / "titles_for_analysis_combined.csv"
DEFAULT_CANDIDATES = PROJECT_ROOT / "data" / "interim" / "11_deidentification" / "deidentification_candidates.csv"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "interim" / "11_deidentification" / "pilot_10pct"
DEFAULT_FRACTION = 0.10
# Frozen after the first pilot attempt so detector revisions use the same rows.
DEFAULT_SEED = "deidentification-v2.3-pilot-2026-08-04"
PILOT_VERSION = "v1.23-auto-mask-all-v2.26-candidates"
STRATUM_FIELDS = ("source_site", "data_round")


def sampling_hash(record_id: str, seed: str) -> str:
    return hashlib.sha256(f"{seed}\0{record_id}".encode()).hexdigest()


def target_sample_size(row_count: int, fraction: float) -> int:
    if not 0 < fraction <= 1:
        raise ValueError("fraction must be greater than 0 and at most 1")
    return min(row_count, int(row_count * fraction + 0.5))


def allocate_stratified_counts(
    stratum_sizes: dict[tuple[str, ...], int],
    fraction: float,
    target_count: int,
) -> dict[tuple[str, ...], int]:
    """Allocate an exact total using proportional largest remainders."""
    exact = {key: size * fraction for key, size in stratum_sizes.items()}
    allocation = {key: min(size, math.floor(exact[key])) for key, size in stratum_sizes.items()}
    remaining = target_count - sum(allocation.values())
    ranked = sorted(
        stratum_sizes,
        key=lambda key: (-(exact[key] - math.floor(exact[key])), key),
    )
    for key in ranked:
        if remaining <= 0:
            break
        if allocation[key] < stratum_sizes[key]:
            allocation[key] += 1
            remaining -= 1
    if remaining:
        raise RuntimeError("unable to allocate requested stratified sample size")
    return allocation


def select_stratified_sample(
    rows: Iterable[dict[str, str]],
    *,
    fraction: float,
    seed: str,
) -> tuple[list[dict[str, str]], dict[tuple[str, ...], int]]:
    row_list = list(rows)
    ids = [row.get("record_id", "") for row in row_list]
    if any(not record_id for record_id in ids):
        raise ValueError("every row must have a non-empty record_id")
    if len(ids) != len(set(ids)):
        raise ValueError("record_id must be unique before pilot sampling")

    strata: dict[tuple[str, ...], list[dict[str, str]]] = defaultdict(list)
    for row in row_list:
        key = tuple(row.get(field, "") for field in STRATUM_FIELDS)
        strata[key].append(row)

    target_count = target_sample_size(len(row_list), fraction)
    allocation = allocate_stratified_counts(
        {key: len(members) for key, members in strata.items()},
        fraction,
        target_count,
    )
    selected_ids: set[str] = set()
    for key, members in strata.items():
        ranked = sorted(members, key=lambda row: (sampling_hash(row["record_id"], seed), row["record_id"]))
        selected_ids.update(row["record_id"] for row in ranked[: allocation[key]])

    selected = [row for row in row_list if row["record_id"] in selected_ids]
    if len(selected) != target_count:
        raise RuntimeError("stratified selection did not produce the exact target size")
    return selected, allocation


def candidate_span_is_valid(title: str, candidate: dict[str, str]) -> bool:
    if candidate.get("text_layer") != "cleaned_title":
        return False
    try:
        start = int(candidate["span_start"])
        end = int(candidate["span_end"])
    except (KeyError, ValueError):
        return False
    matched_text = candidate.get("matched_text", "")
    return bool(matched_text) and 0 <= start < end <= len(title) and title[start:end] == matched_text


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--fraction", type=float, default=DEFAULT_FRACTION)
    parser.add_argument("--seed", default=DEFAULT_SEED)
    args = parser.parse_args()

    with args.input.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        input_fieldnames = reader.fieldnames or []
    with args.candidates.open("r", encoding="utf-8", newline="") as f:
        candidate_reader = csv.DictReader(f)
        candidate_rows = list(candidate_reader)
        candidate_fieldnames = candidate_reader.fieldnames or []

    sampled_rows, allocation = select_stratified_sample(rows, fraction=args.fraction, seed=args.seed)
    sampled_ids = {row["record_id"] for row in sampled_rows}
    sampled_candidates = [row for row in candidate_rows if row.get("record_id", "") in sampled_ids]
    candidates_by_record: dict[str, list[dict[str, str]]] = defaultdict(list)
    for candidate in sampled_candidates:
        candidates_by_record[candidate.get("record_id", "")].append(candidate)
    excluded_ids = political_exclusion_record_ids(sampled_candidates)

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    full_output_path = output_dir / "pilot_deidentified_10pct.csv"
    changed_output_path = output_dir / "pilot_changed_rows_for_review.csv"
    candidate_output_path = output_dir / "pilot_candidates.csv"
    replacement_log_path = output_dir / "pilot_replacements.jsonl"
    exclusions_path = output_dir / "pilot_political_exclusions.csv"
    exclusion_fieldnames = ["record_id", "source_site", "data_round", "original_title", "cleaned_title", "reason"]

    pilot_fields = [
        "pilot_sampling_hash",
        "pilot_sampling_stratum",
        "pilot_policy",
        "pilot_candidate_count",
        "pilot_replacement_count",
        "pilot_signal_types",
        "pilot_confidences",
        "deidentified_title",
        "deidentification_applied",
        "pilot_review_decision",
        "pilot_review_note",
    ]
    output_fieldnames = input_fieldnames + pilot_fields
    output_rows: list[dict[str, str | int | bool]] = []
    changed_rows: list[dict[str, str | int | bool]] = []
    invalid_candidate_count = 0
    valid_candidate_count = 0
    applied_span_count = 0
    replacement_counter: Counter[str] = Counter()
    confidence_counter: Counter[str] = Counter()
    signal_counter: Counter[str] = Counter()

    with replacement_log_path.open("w", encoding="utf-8") as log_f, exclusions_path.open(
        "w", encoding="utf-8", newline=""
    ) as excl_f:
        excl_writer = csv.DictWriter(excl_f, fieldnames=exclusion_fieldnames)
        excl_writer.writeheader()
        for row in sampled_rows:
            if row["record_id"] in excluded_ids:
                excl_writer.writerow(
                    {
                        "record_id": row["record_id"],
                        "source_site": row.get("source_site", ""),
                        "data_round": row.get("data_round", ""),
                        "original_title": row.get("original_title", ""),
                        "cleaned_title": row.get("cleaned_title", ""),
                        "reason": POLITICAL_EXCLUSION_SIGNAL,
                    }
                )
                continue
            title = row.get("cleaned_title", "")
            decisions = candidates_by_record.get(row["record_id"], [])
            valid_decisions = []
            for decision in decisions:
                if candidate_span_is_valid(title, decision):
                    valid_decisions.append(decision)
                    valid_candidate_count += 1
                    confidence_counter.update([decision.get("confidence", "")])
                    signal_counter.update(decision.get("signal_type", "").split("|"))
                else:
                    invalid_candidate_count += 1

            deidentified_title, applied = apply_span_replacements(title, valid_decisions)
            applied_span_count += len(applied)
            for item in applied:
                replacement_counter.update([item["suggested_replacement"]])

            signal_types = sorted(
                {signal for decision in valid_decisions for signal in decision.get("signal_type", "").split("|") if signal}
            )
            confidences = sorted({decision.get("confidence", "") for decision in valid_decisions if decision.get("confidence", "")})
            out_row: dict[str, str | int | bool] = dict(row)
            out_row.update(
                {
                    "pilot_sampling_hash": sampling_hash(row["record_id"], args.seed),
                    "pilot_sampling_stratum": "|".join(row.get(field, "") for field in STRATUM_FIELDS),
                    "pilot_policy": PILOT_VERSION,
                    "pilot_candidate_count": len(valid_decisions),
                    "pilot_replacement_count": len(applied),
                    "pilot_signal_types": "|".join(signal_types),
                    "pilot_confidences": "|".join(confidences),
                    "deidentified_title": deidentified_title,
                    "deidentification_applied": bool(applied),
                    "pilot_review_decision": "",
                    "pilot_review_note": "",
                }
            )
            output_rows.append(out_row)
            if applied:
                changed_rows.append(out_row)
                log_f.write(
                    json.dumps(
                        {
                            "record_id": row["record_id"],
                            "source_site": row.get("source_site", ""),
                            "data_round": row.get("data_round", ""),
                            "cleaned_title": title,
                            "deidentified_title": deidentified_title,
                            "replacements": applied,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )

    for path, selected_rows in ((full_output_path, output_rows), (changed_output_path, changed_rows)):
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=output_fieldnames)
            writer.writeheader()
            writer.writerows(selected_rows)

    pilot_candidate_fieldnames = candidate_fieldnames + ["pilot_decision"]
    with candidate_output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=pilot_candidate_fieldnames)
        writer.writeheader()
        for candidate in sampled_candidates:
            writer.writerow({**candidate, "pilot_decision": "auto_accept_for_pilot_review"})

    stratum_input_counts = Counter(tuple(row.get(field, "") for field in STRATUM_FIELDS) for row in rows)
    summary = {
        "status": "pilot_auto_masked_pending_human_review",
        "pilot_version": PILOT_VERSION,
        "input_path": str(args.input.relative_to(PROJECT_ROOT)),
        "input_sha256": sha256_file(args.input),
        "candidate_path": str(args.candidates.relative_to(PROJECT_ROOT)),
        "candidate_sha256": sha256_file(args.candidates),
        "fraction": args.fraction,
        "seed": args.seed,
        "stratum_fields": list(STRATUM_FIELDS),
        "input_rows": len(rows),
        "target_sample_rows": target_sample_size(len(rows), args.fraction),
        "sample_rows": len(sampled_rows),
        "sample_rows_excluded_political": len(excluded_ids),
        "sample_rows_output": len(sampled_rows) - len(excluded_ids),
        "sample_rows_with_candidates": len(candidates_by_record),
        "sample_rows_changed": len(changed_rows),
        "sample_candidate_rows": len(sampled_candidates),
        "valid_candidate_rows": valid_candidate_count,
        "invalid_candidate_rows": invalid_candidate_count,
        "applied_spans_after_overlap_resolution": applied_span_count,
        "replacement_counts": dict(sorted(replacement_counter.items())),
        "candidate_confidences": dict(sorted((key, value) for key, value in confidence_counter.items() if key)),
        "candidate_signals": dict(sorted((key, value) for key, value in signal_counter.items() if key)),
        "strata": [
            {
                "source_site": key[0],
                "data_round": key[1],
                "input_rows": stratum_input_counts[key],
                "sample_rows": allocation[key],
            }
            for key in sorted(allocation)
        ],
        "output_full_path": str(full_output_path.relative_to(PROJECT_ROOT)),
        "output_full_sha256": sha256_file(full_output_path),
        "output_changed_path": str(changed_output_path.relative_to(PROJECT_ROOT)),
        "output_changed_sha256": sha256_file(changed_output_path),
        "output_candidates_path": str(candidate_output_path.relative_to(PROJECT_ROOT)),
        "output_candidates_sha256": sha256_file(candidate_output_path),
        "replacement_log_path": str(replacement_log_path.relative_to(PROJECT_ROOT)),
        "replacement_log_sha256": sha256_file(replacement_log_path),
        "political_exclusions_path": str(exclusions_path.relative_to(PROJECT_ROOT)),
        "political_exclusions_sha256": sha256_file(exclusions_path),
    }
    summary_path = output_dir / "pilot_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
