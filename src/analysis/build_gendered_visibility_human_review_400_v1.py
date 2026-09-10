"""Build the frozen, private 400-record human audit package for reviewer 1."""

from __future__ import annotations

import csv
import hashlib
import json
import random
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.analysis.analyze_gendered_visibility_paper_corpus_v1 import (
    FREEZE_MANIFEST,
    MEMBERSHIP,
    ROOT,
    Unit,
    load_units,
)

SCRIPT = Path(__file__).resolve()
CODEBOOK = ROOT / "config/gendered_visibility_human_review_400_v1.json"
OUTPUT_DIR = ROOT / "data/interim/72_gendered_visibility_human_review_400_v1"
SEED = 2_026_081_701
QUOTAS = {
    "later_probability": 300,
    "early_probability": 50,
    "mechanism_enriched": 50,
}
REFERENCE_FIELDS = (
    "record_validity",
    "privacy_chain_relevance",
    "material_creation_visibility",
    "material_creation_target_position",
    "distribution_visibility",
    "distribution_actor_position",
    "distribution_actor_realization",
    "distribution_target_position",
    "distribution_authorization_visibility",
    "exposure_visibility",
    "exposure_target_position",
    "objectification_target_position",
    "desire_holder_position",
    "pleasure_holder_position",
    "refusal_resistance_position",
    "attributed_speech_position",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def opaque(prefix: str, corpus_index: int) -> str:
    token = hashlib.sha256(f"{SEED}|{prefix}|{corpus_index}".encode()).hexdigest()
    return f"{prefix}-{token[:12].upper()}"


def stratum(unit: Unit) -> str:
    if unit.source_layer == "run70_complete":
        return "later_probability"
    if unit.source_layer == "frozen_v0_3_5000" and unit.sampling_track == "probability":
        return "early_probability"
    if unit.source_layer == "frozen_v0_3_5000" and unit.sampling_track == "mechanism":
        return "mechanism_enriched"
    raise RuntimeError(f"unrecognized corpus stratum at {unit.corpus_index}")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
        ),
        encoding="utf-8",
    )


def build() -> dict[str, Any]:
    units, freeze = load_units()
    members = {
        int(row["corpus_index"]): row
        for row in csv.DictReader(MEMBERSHIP.open(encoding="utf-8", newline=""))
    }
    frames: dict[str, list[Unit]] = {name: [] for name in QUOTAS}
    for unit in units:
        frames[stratum(unit)].append(unit)
    frame_counts = {name: len(frame) for name, frame in frames.items()}
    if frame_counts != {
        "later_probability": 33_628,
        "early_probability": 3_000,
        "mechanism_enriched": 1_995,
    }:
        raise RuntimeError(f"sampling-frame counts drifted: {frame_counts}")

    sampled: list[tuple[str, Unit]] = []
    for offset, (name, quota) in enumerate(QUOTAS.items()):
        rng = random.Random(SEED + offset)
        sampled.extend((name, unit) for unit in rng.sample(frames[name], quota))
    random.Random(SEED + 99).shuffle(sampled)

    queue_rows: list[dict[str, Any]] = []
    mapping_rows: list[dict[str, Any]] = []
    reference_rows: list[dict[str, Any]] = []
    sampling_rows: list[dict[str, Any]] = []
    for position, (name, unit) in enumerate(sampled, start=1):
        blind_id = opaque("GV", unit.corpus_index)
        review_record_id = opaque("R1", unit.corpus_index)
        member = members[unit.corpus_index]
        queue_rows.append(
            {
                "record_id": review_record_id,
                "blind_id": blind_id,
                "position": position,
                "deidentified_title": unit.title,
                "cluster_id": f"TH-{unit.title_sha256[:16].upper()}",
            }
        )
        mapping_rows.append(
            {
                "blind_id": blind_id,
                "review_record_id": review_record_id,
                "corpus_index": unit.corpus_index,
                "source_layer": unit.source_layer,
                "source_item_index": member["source_item_index"],
                "sampling_track": unit.sampling_track,
                "record_key_sha256": member["record_key_sha256"],
                "title_sha256": unit.title_sha256,
                "audit_stratum": name,
            }
        )
        reference_rows.append(
            {
                "blind_id": blind_id,
                "reference_output": {
                    field: unit.output[field] for field in REFERENCE_FIELDS
                },
            }
        )
        probability = QUOTAS[name] / frame_counts[name]
        sampling_rows.append(
            {
                "blind_id": blind_id,
                "audit_stratum": name,
                "stratum_frame_n": frame_counts[name],
                "stratum_sample_n": QUOTAS[name],
                "inclusion_probability": f"{probability:.12f}",
                "design_weight": f"{1 / probability:.12f}",
            }
        )

    private_dir = OUTPUT_DIR / "private"
    queue_path = private_dir / "blind_queue.csv"
    mapping_path = private_dir / "blind_mapping.csv"
    reference_path = private_dir / "sealed_reference.jsonl"
    sampling_path = OUTPUT_DIR / "sampling_frame.csv"
    write_csv(queue_path, queue_rows)
    write_csv(mapping_path, mapping_rows)
    write_jsonl(reference_path, reference_rows)
    write_csv(sampling_path, sampling_rows)
    for path in (queue_path, mapping_path, reference_path):
        path.chmod(0o600)

    duplicate_groups = Counter(row["title_sha256"] for row in mapping_rows)
    repeated_hash_groups = sum(count > 1 for count in duplicate_groups.values())
    repeated_records_after_first = sum(count - 1 for count in duplicate_groups.values())
    inputs = {
        path.relative_to(ROOT).as_posix(): sha256_file(path)
        for path in (SCRIPT, CODEBOOK, FREEZE_MANIFEST, MEMBERSHIP)
    }
    for path_text, digest in freeze["inputs"].items():
        inputs[path_text] = digest
    outputs = {
        path.relative_to(ROOT).as_posix(): sha256_file(path)
        for path in (queue_path, mapping_path, reference_path, sampling_path)
    }
    manifest = {
        "review_id": "gendered_visibility_human_review_400_v1",
        "status": "frozen_two_reviewers_not_started",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "corpus": "gendered_visibility_paper_corpus_v1",
        "corpus_records": len(units),
        "sample_records": len(sampled),
        "sampling_unit": "title_record",
        "sampling_method": "independent fixed-seed simple random sampling within three design strata",
        "seed": SEED,
        "frame_counts": frame_counts,
        "quotas": QUOTAS,
        "analysis_requirement": "use audit-stratum design weights for corpus-wide estimates",
        "rare_field_scope": "directional_or_inconclusive_when_effective_denominator_is_small",
        "duplicate_title_policy": "allowed because the estimand and sampling unit are title records",
        "sample_duplicate_title_hash_groups": repeated_hash_groups,
        "sample_duplicate_records_after_first": repeated_records_after_first,
        "reviewers": ["reviewer_1", "reviewer_2"],
        "reviewer_independence": "same frozen records; coder-specific order, SQLite database, events, and export",
        "machine_predictions_exposed_to_reviewer": False,
        "sampling_strata_exposed_to_reviewer": False,
        "source_metadata_exposed_to_reviewer": False,
        "inputs": dict(sorted(inputs.items())),
        "outputs": dict(sorted(outputs.items())),
    }
    manifest_path = OUTPUT_DIR / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    manifest = build()
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
