"""Build the fixed 100-record low-cost DeepSeek bridge benchmark.

The reference is deterministically projected from the frozen 5,000-record v0.3
machine output.  It is a model-bridge reference, not human gold.  The protected
``final_blind_test`` split is excluded before any candidate ranking.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.analysis.gendered_argument_visibility_benchmark_contract_v1 import (
    DEFAULT_CODEBOOK,
    DEFAULT_PROMPT,
    DEFAULT_SCHEMA,
    compact_output,
    load_json,
    validate_compact_item,
    validate_contract_files,
)

ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = ROOT / "data/interim/51_gendered_argument_visibility_scaleup_5000_v1"
SOURCE_SAMPLE = SOURCE_DIR / "private/sample_manifest.csv"
SOURCE_FINAL = SOURCE_DIR / "private/validation_5000_final_records.jsonl"
SOURCE_FINAL_MANIFEST = SOURCE_DIR / "validation_5000_final_manifest.json"
CONFIG = ROOT / "config/gendered_argument_visibility_model_benchmark_v1.json"
RUN_DIR = ROOT / "data/interim/52_gendered_argument_visibility_model_benchmark_v1"
PRIVATE = RUN_DIR / "private"
PRIVATE_ITEMS = PRIVATE / "benchmark_items.jsonl"
PUBLIC_COUNTS = RUN_DIR / "sample_counts.csv"
PUBLIC_MANIFEST = RUN_DIR / "sample_manifest.json"

Predicate = Callable[[dict[str, Any]], bool]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_rank(seed: int, stratum: str, title_sha256: str) -> str:
    value = f"{seed}\x1f{stratum}\x1f{title_sha256}".encode()
    return hashlib.sha256(value).hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def atomic_json(path: Path, value: Any, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def atomic_jsonl(path: Path, rows: list[dict[str, Any]], mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
        temporary = Path(handle.name)
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def atomic_csv(
    path: Path, rows: list[dict[str, Any]], fields: list[str], mode: int = 0o644
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="", dir=path.parent, delete=False
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
        temporary = Path(handle.name)
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def clean_reference(record: dict[str, Any]) -> bool:
    output = record.get("output") or {}
    derived = record.get("derived") or {}
    return bool(
        record.get("valid")
        and output.get("record_validity") == "valid"
        and derived.get("evidence_complete") is True
        and not record.get("normalization_actions")
        and output.get("needs_adjudication") is False
    )


def diagnostic_flags(record: dict[str, Any]) -> tuple[bool, bool, bool]:
    output = record.get("output") or {}
    derived = record.get("derived") or {}
    return (
        bool(record.get("normalization_actions")),
        derived.get("evidence_complete") is not True,
        bool(output.get("needs_adjudication")),
    )


def mainline_opportunity(output: dict[str, Any]) -> bool:
    return output.get("distribution_visibility") == "visible"


def _visible(output: dict[str, Any], field: str) -> bool:
    return output.get(field) == "visible"


def _subjectivity_feminine(output: dict[str, Any]) -> bool:
    return any(
        output.get(field) == "feminine"
        for field in (
            "desire_holder_position",
            "pleasure_holder_position",
            "refusal_resistance_position",
            "attributed_speech_position",
        )
    )


CLEAN_STRATA: dict[str, Predicate] = {
    "b1_actor_visible": lambda output: _visible(output, "distribution_visibility")
    and output.get("distribution_actor_position")
    not in {"not_visible", "unclear", "not_applicable"},
    "c4_authorization_marked": lambda output: _visible(
        output, "distribution_visibility"
    )
    and output.get("distribution_authorization_visibility")
    in {"authorized_explicit", "unauthorized_explicit", "mixed_or_stage_conflict"},
    "c2_three_stage_visible": lambda output: all(
        _visible(output, field)
        for field in (
            "material_creation_visibility",
            "distribution_visibility",
            "exposure_visibility",
        )
    ),
    "c2_creation_distribution_visible": lambda output: _visible(
        output, "material_creation_visibility"
    )
    and _visible(output, "distribution_visibility"),
    "c5_feminine_objectification_or_subjectivity": lambda output: _visible(
        output, "distribution_visibility"
    )
    and output.get("distribution_target_position") == "feminine"
    and (
        output.get("objectification_target_position") == "feminine"
        or _subjectivity_feminine(output)
    ),
    "c2_distribution_exposure_visible": lambda output: _visible(
        output, "distribution_visibility"
    )
    and _visible(output, "exposure_visibility"),
    "b1_actor_omitted": lambda output: _visible(output, "distribution_visibility")
    and output.get("distribution_actor_position") == "not_visible",
    "c1_feminine_distribution_target": lambda output: _visible(
        output, "distribution_visibility"
    )
    and output.get("distribution_target_position") == "feminine",
}


def challenge_stratum(record: dict[str, Any], stratum: str) -> bool:
    normalized, incomplete, adjudication = diagnostic_flags(record)
    if stratum == "diagnostic_adjudication":
        return adjudication
    if stratum == "diagnostic_evidence_incomplete_without_normalization":
        return incomplete and not normalized and not adjudication
    if stratum == "diagnostic_normalized_evidence_complete":
        return normalized and not incomplete and not adjudication
    if stratum == "diagnostic_normalized_and_evidence_incomplete":
        return normalized and incomplete and not adjudication
    raise KeyError(f"unknown challenge stratum: {stratum}")


def select_by_strata(
    candidates: list[dict[str, Any]],
    strata: list[str],
    quota: int,
    seed: int,
    sample_by_index: dict[int, dict[str, str]],
    *,
    challenge: bool,
) -> list[tuple[str, dict[str, Any]]]:
    selected: list[tuple[str, dict[str, Any]]] = []
    used: set[int] = set()
    for stratum in strata:
        if challenge:
            eligible = [
                record
                for record in candidates
                if record["item_index"] not in used
                and challenge_stratum(record, stratum)
            ]
        else:
            predicate = CLEAN_STRATA[stratum]
            eligible = [
                record
                for record in candidates
                if record["item_index"] not in used
                and predicate(record["output"])
            ]
        eligible.sort(
            key=lambda record: stable_rank(
                seed,
                stratum,
                sample_by_index[record["item_index"]]["title_sha256"],
            )
        )
        if len(eligible) < quota:
            raise RuntimeError(
                f"stratum {stratum} has {len(eligible)} unused candidates; need {quota}"
            )
        chosen = eligible[:quota]
        selected.extend((stratum, record) for record in chosen)
        used.update(record["item_index"] for record in chosen)
    return selected


def build() -> dict[str, Any]:
    contract = validate_contract_files()
    codebook = contract["codebook"]
    config = load_json(CONFIG)
    samples = read_csv(SOURCE_SAMPLE)
    sample_by_index = {int(row["item_index"]): row for row in samples}
    records = read_jsonl(SOURCE_FINAL)
    if len(samples) != 5000 or len(records) != 5000:
        raise RuntimeError("frozen source must contain exactly 5,000 samples and records")
    if len(sample_by_index) != 5000 or len({row["cluster_id"] for row in samples}) != 5000:
        raise RuntimeError("frozen source indices and clusters must be unique")
    if {record["item_index"] for record in records} != set(sample_by_index):
        raise RuntimeError("source final records and sample indices differ")

    excluded_splits = set(config["sample"]["exclude_frozen_splits"])
    eligible = [
        record
        for record in records
        if sample_by_index[record["item_index"]]["frozen_split"] not in excluded_splits
    ]
    clean_candidates = [record for record in eligible if clean_reference(record)]
    challenge_candidates = [
        record
        for record in eligible
        if record.get("valid")
        and (record.get("output") or {}).get("record_validity") == "valid"
        and mainline_opportunity(record["output"])
        and any(diagnostic_flags(record))
    ]
    seed = int(config["seed"])
    clean = select_by_strata(
        clean_candidates,
        list(config["sample"]["clean_strata_order"]),
        int(config["sample"]["clean_per_stratum"]),
        seed,
        sample_by_index,
        challenge=False,
    )
    clean_source_indices = {record["item_index"] for _, record in clean}
    challenge_candidates = [
        record
        for record in challenge_candidates
        if record["item_index"] not in clean_source_indices
    ]
    challenge = select_by_strata(
        challenge_candidates,
        list(config["sample"]["challenge_strata_order"]),
        int(config["sample"]["challenge_per_stratum"]),
        seed,
        sample_by_index,
        challenge=True,
    )
    selected = [
        ("clean_bridge", stratum, record) for stratum, record in clean
    ] + [
        ("diagnostic_challenge", stratum, record) for stratum, record in challenge
    ]
    if len(selected) != int(config["records"]):
        raise RuntimeError(f"selected {len(selected)} records, expected {config['records']}")

    output_rows: list[dict[str, Any]] = []
    for benchmark_index, (partition, stratum, source_record) in enumerate(selected, 1):
        source_index = int(source_record["item_index"])
        sample = sample_by_index[source_index]
        if sample["frozen_split"] in excluded_splits:
            raise RuntimeError("protected final blind-test record reached benchmark output")
        reference = compact_output(
            source_record["output"],
            codebook,
            item_index=benchmark_index,
            title=sample["deidentified_title"],
        )
        validation = validate_compact_item(
            reference,
            sample["deidentified_title"],
            codebook,
            require_complete_evidence=partition == "clean_bridge",
        )
        normalized, incomplete, adjudication = diagnostic_flags(source_record)
        output_rows.append(
            {
                "benchmark_item_index": benchmark_index,
                "partition": partition,
                "benchmark_stratum": stratum,
                "source_run": "51_gendered_argument_visibility_scaleup_5000_v1",
                "source_item_index": source_index,
                "record_id": sample["record_id"],
                "cluster_id": sample["cluster_id"],
                "source_site": sample["source_site"],
                "frozen_split": sample["frozen_split"],
                "sampling_track": sample["sampling_track"],
                "sampling_stratum": sample["sampling_stratum"],
                "incremental_wave": sample["incremental_wave"],
                "semantic_length": int(sample["semantic_length"]),
                "length_band": sample["length_band"],
                "title_sha256": sample["title_sha256"],
                "deidentified_title": sample["deidentified_title"],
                "reference_compact": reference,
                "reference_diagnostics": {
                    "source_normalized": normalized,
                    "source_evidence_incomplete": incomplete,
                    "source_needs_adjudication": adjudication,
                    "reduced_evidence_complete": validation["evidence_complete"],
                },
            }
        )

    if len({row["source_item_index"] for row in output_rows}) != len(output_rows):
        raise RuntimeError("benchmark contains duplicate source items")
    if len({row["cluster_id"] for row in output_rows}) != len(output_rows):
        raise RuntimeError("benchmark contains duplicate expression clusters")
    PRIVATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(PRIVATE, 0o700)
    atomic_jsonl(PRIVATE_ITEMS, output_rows, 0o600)

    counts = Counter((row["partition"], row["benchmark_stratum"]) for row in output_rows)
    count_rows = [
        {"partition": partition, "benchmark_stratum": stratum, "records": count}
        for (partition, stratum), count in sorted(counts.items())
    ]
    atomic_csv(
        PUBLIC_COUNTS,
        count_rows,
        ["partition", "benchmark_stratum", "records"],
    )
    inputs = {
        str(path.relative_to(ROOT)): sha256(path)
        for path in (
            SOURCE_SAMPLE,
            SOURCE_FINAL,
            SOURCE_FINAL_MANIFEST,
            CONFIG,
            DEFAULT_CODEBOOK,
            DEFAULT_SCHEMA,
            DEFAULT_PROMPT,
        )
    }
    manifest = {
        "created_at": utc_now(),
        "status": "prepared_not_run",
        "version": config["version"],
        "reference_role": config["reference_role"],
        "reference_warning": (
            "Frozen v0.3 machine bridge only; agreement is not human-gold accuracy. "
            "The 20 diagnostic challenge records are never used for primary selection."
        ),
        "records": len(output_rows),
        "partitions": dict(sorted(Counter(row["partition"] for row in output_rows).items())),
        "unique_source_items": len({row["source_item_index"] for row in output_rows}),
        "unique_expression_clusters": len({row["cluster_id"] for row in output_rows}),
        "excluded_frozen_splits": sorted(excluded_splits),
        "excluded_split_selected_records": sum(
            row["frozen_split"] in excluded_splits for row in output_rows
        ),
        "selection": {
            "seed": seed,
            "clean_definition": (
                "source valid; record_validity=valid; derived.evidence_complete=true; "
                "normalization_actions empty; needs_adjudication=false"
            ),
            "clean_strata_order": config["sample"]["clean_strata_order"],
            "challenge_definition": (
                "source valid mainline distribution opportunity with normalization, "
                "evidence-incompleteness, or adjudication diagnostic flag"
            ),
            "challenge_strata_order": config["sample"]["challenge_strata_order"],
        },
        "contract": {
            "fields": 17,
            "compact_keys_and_values": True,
            "exact_evidence_substrings_required": True,
            "deterministic_long_field_expansion": True,
        },
        "inputs": inputs,
        "outputs": {
            str(PRIVATE_ITEMS.relative_to(ROOT)): sha256(PRIVATE_ITEMS),
            str(PUBLIC_COUNTS.relative_to(ROOT)): sha256(PUBLIC_COUNTS),
        },
        "privacy": (
            "Titles, IDs, source-item links, and reference outputs are private. "
            "Public files contain aggregate counts and checksums only."
        ),
        "api_calls": 0,
    }
    atomic_json(PUBLIC_MANIFEST, manifest)
    return manifest


def main() -> None:
    manifest = build()
    print(
        json.dumps(
            {
                "records": manifest["records"],
                "partitions": manifest["partitions"],
                "excluded_split_selected_records": manifest[
                    "excluded_split_selected_records"
                ],
                "api_calls": 0,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
