"""Build the immutable 61,016-record frame for full annotation run 55.

This module is offline-only. It excludes the frozen v0.3 sample by record ID,
joins the frozen corpus split/cluster metadata, assigns deterministic stratified
router waves, and writes only to ``data/interim/55_*``.
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from src.analysis.build_gendered_argument_visibility_model_benchmark_v1 import (
    atomic_csv,
    atomic_json,
    atomic_jsonl,
    sha256,
    utc_now,
)
from src.analysis.build_gendered_argument_visibility_scaleup_5000_v1 import (
    bounded_hamilton,
    stable_rank,
)

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config/gendered_asymmetric_visibility_full_annotation_v1.json"
RUN_DIR = ROOT / "data/interim/55_gendered_asymmetric_visibility_full_annotation_v1"
PRIVATE_DIR = RUN_DIR / "private"
FRAME = PRIVATE_DIR / "remaining_frame.jsonl"
CELL_COUNTS = RUN_DIR / "frame_counts.csv"
MANIFEST = RUN_DIR / "frame_manifest.json"
BUILDER = (
    ROOT / "src/analysis/build_gendered_asymmetric_visibility_full_annotation_v1.py"
)
EXPECTED_EXPERIMENT_ID = "55_gendered_asymmetric_visibility_full_annotation_v1"


def load_config() -> dict[str, Any]:
    value = json.loads(CONFIG.read_text(encoding="utf-8"))
    if value.get("experiment_id") != EXPECTED_EXPERIMENT_ID:
        raise RuntimeError("unexpected experiment id")
    if value.get("approval", {}).get("provider_calls_authorized") is not False:
        raise RuntimeError(
            "frame build is permitted only while provider calls remain disabled"
        )
    return value


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _input_path(config: dict[str, Any], key: str) -> Path:
    return ROOT / str(config["inputs"][key])


def _allocate_wave(
    remaining: dict[tuple[str, str, str], list[dict[str, Any]]],
    count: int,
) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    populations = {cell: len(rows) for cell, rows in remaining.items() if rows}
    if count < 0 or count > sum(populations.values()):
        raise RuntimeError("router wave count is infeasible")
    minima = {cell: 1 for cell in populations} if count >= len(populations) else {}
    quotas = bounded_hamilton(populations, count, minima)
    selected: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for cell in sorted(populations):
        take = quotas[cell]
        selected[cell] = remaining[cell][:take]
        del remaining[cell][:take]
    return selected


def assign_router_waves(
    rows: list[dict[str, Any]], config: dict[str, Any]
) -> list[dict[str, Any]]:
    """Assign exact cumulative wave counts with stable within-cell ordering."""
    seed = int(config["sampling"]["seed"])
    by_cell: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        cell = (row["frozen_split"], row["source_site"], row["length_band"])
        by_cell[cell].append(row)
    for cell, candidates in by_cell.items():
        candidates.sort(
            key=lambda row: stable_rank(
                seed,
                *cell,
                row["title_sha256"],
                row["record_id"],
            )
        )

    assigned: list[dict[str, Any]] = []
    prior = 0
    for wave in config["sampling"]["router_waves"]:
        cumulative = int(wave["cumulative_records"])
        increment = cumulative - prior
        if increment <= 0:
            raise RuntimeError(
                "router cumulative wave sizes must be strictly increasing"
            )
        selected = _allocate_wave(by_cell, increment)
        for cell in sorted(selected):
            for row in selected[cell]:
                assigned.append(dict(row) | {"router_wave": wave["id"]})
        prior = cumulative
    if any(by_cell.values()) or len(assigned) != len(rows):
        raise RuntimeError("router waves did not exhaust the remaining frame")

    wave_order = {
        wave["id"]: position
        for position, wave in enumerate(config["sampling"]["router_waves"], start=1)
    }
    assigned.sort(
        key=lambda row: (
            wave_order[row["router_wave"]],
            stable_rank(seed, "dispatch", row["title_sha256"], row["record_id"]),
        )
    )
    for index, row in enumerate(assigned, start=1):
        row["production_item_index"] = index
        row["batch_id"] = f"router_batch_{(index - 1) // 20 + 1:06d}"
        row["deep_order_hash"] = stable_rank(
            seed,
            "deep",
            row["frozen_split"],
            row["source_site"],
            row["length_band"],
            row["title_sha256"],
        )
    return assigned


def build() -> dict[str, Any]:
    config = load_config()
    full_path = _input_path(config, "full_manifest")
    sample_path = _input_path(config, "frozen_v0_3_sample_manifest")
    cluster_path = _input_path(config, "cluster_split_assignments")
    full = read_csv(full_path)
    sample = read_csv(sample_path)
    cluster_rows = read_csv(cluster_path)
    expected = config["scope"]
    if len(full) != int(expected["full_corpus_records"]):
        raise RuntimeError("full corpus count drifted")
    if len(sample) != int(expected["reuse_frozen_v0_3_records"]):
        raise RuntimeError("frozen v0.3 sample count drifted")
    if len({row["record_id"] for row in full}) != len(full):
        raise RuntimeError("full manifest record_id is not unique")
    if len({row["record_id"] for row in sample}) != len(sample):
        raise RuntimeError("v0.3 sample record_id is not unique")
    cluster_by_id = {row["record_id"]: row for row in cluster_rows}
    if len(cluster_by_id) != len(cluster_rows):
        raise RuntimeError("cluster assignment record_id is not unique")

    sample_ids = {row["record_id"] for row in sample}
    full_ids = {row["record_id"] for row in full}
    if not sample_ids <= full_ids:
        raise RuntimeError("v0.3 sample is not a subset of the 66,016 corpus")
    remaining: list[dict[str, Any]] = []
    for row in full:
        if row["record_id"] in sample_ids:
            continue
        metadata = cluster_by_id.get(row["record_id"])
        if metadata is None:
            raise RuntimeError(
                f"missing cluster/split metadata for item {row['item_index']}"
            )
        remaining.append(
            {
                "full_item_index": int(row["item_index"]),
                "record_id": row["record_id"],
                "title_sha256": row["title_sha256"],
                "deidentified_title": row["deidentified_title"],
                "source_site": metadata["source_site"],
                "data_round": metadata["data_round"],
                "frozen_split": metadata["frozen_split"],
                "cluster_id": metadata["cluster_id"],
                "cluster_size": int(metadata["cluster_size"]),
                "semantic_length": int(metadata["semantic_length"]),
                "length_band": metadata["length_band"],
            }
        )
    if len(remaining) != int(expected["new_router_records"]):
        raise RuntimeError("remaining production count drifted")
    assigned = assign_router_waves(remaining, config)

    PRIVATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    PRIVATE_DIR.chmod(0o700)
    atomic_jsonl(FRAME, assigned, 0o600)
    counts = Counter(
        (
            row["router_wave"],
            row["frozen_split"],
            row["source_site"],
            row["length_band"],
        )
        for row in assigned
    )
    count_rows = [
        {
            "router_wave": key[0],
            "frozen_split": key[1],
            "source_site": key[2],
            "length_band": key[3],
            "records": value,
        }
        for key, value in sorted(counts.items())
    ]
    atomic_csv(
        CELL_COUNTS,
        count_rows,
        ["router_wave", "frozen_split", "source_site", "length_band", "records"],
    )
    wave_counts = Counter(row["router_wave"] for row in assigned)
    inputs = {
        str(path.relative_to(ROOT)): sha256(path)
        for path in (CONFIG, full_path, sample_path, cluster_path, BUILDER)
    }
    manifest = {
        "created_at": utc_now(),
        "status": "offline_frame_built_provider_calls_not_authorized",
        "version": config["version"],
        "experiment_id": config["experiment_id"],
        "full_corpus_records": len(full),
        "reused_v0_3_records": len(sample),
        "new_router_records": len(assigned),
        "excluded_as_frozen_v0_3": len(sample_ids),
        "missing_cluster_metadata": 0,
        "duplicate_record_ids": 0,
        "router_wave_counts": dict(sorted(wave_counts.items())),
        "stratification": config["sampling"]["router_strata"],
        "selection": config["sampling"]["ordering"],
        "seed": config["sampling"]["seed"],
        "input_hashes": inputs,
        "outputs": {
            str(FRAME.relative_to(ROOT)): sha256(FRAME),
            str(CELL_COUNTS.relative_to(ROOT)): sha256(CELL_COUNTS),
        },
        "privacy": "The private frame contains titles and record IDs; the public count table and manifest do not.",
        "api_calls": 0,
    }
    atomic_json(MANIFEST, manifest)
    return manifest


def main() -> None:
    manifest = build()
    print(
        json.dumps(
            {
                "new_router_records": manifest["new_router_records"],
                "router_wave_counts": manifest["router_wave_counts"],
                "frame_sha256": manifest["outputs"][str(FRAME.relative_to(ROOT))],
                "api_calls": 0,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
