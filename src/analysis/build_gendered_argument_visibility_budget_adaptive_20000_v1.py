"""Build the nested 4k -> 8k -> 20k -> census probability sequence."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from src.analysis.build_gendered_argument_visibility_model_benchmark_v1 import (
    ROOT,
    atomic_csv,
    atomic_json,
    atomic_jsonl,
    read_jsonl,
    sha256,
    utc_now,
)
from src.analysis.build_gendered_argument_visibility_scaleup_5000_v1 import (
    bounded_hamilton,
    stable_rank,
)

CONFIG = ROOT / "config/gendered_argument_visibility_budget_adaptive_20000_v1.json"
RUN_DIR = ROOT / "data/interim/70_gendered_argument_visibility_budget_adaptive_20000_v1"
PRIVATE_DIR = RUN_DIR / "private"
FRAME = PRIVATE_DIR / "probability_sequence_85727.jsonl"
STRATUM_COUNTS = RUN_DIR / "stratum_inclusion_counts.csv"
WAVE_SUMMARY = RUN_DIR / "wave_summary.csv"
COST_PROJECTION = RUN_DIR / "cost_projection_cny.csv"
MANIFEST = RUN_DIR / "frame_manifest.json"
BUILDER = Path(__file__).resolve()
EXPECTED_EXPERIMENT_ID = "70_gendered_argument_visibility_budget_adaptive_20000_v1"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def load_config() -> dict[str, Any]:
    value = json.loads(CONFIG.read_text(encoding="utf-8"))
    if value.get("experiment_id") != EXPECTED_EXPERIMENT_ID:
        raise RuntimeError("unexpected run-70 experiment id")
    if value.get("authorization", {}).get("provider_calls_authorized") is not False:
        raise RuntimeError("offline frame build requires production calls disabled")
    waves = [int(row["cumulative_records"]) for row in value["sampling"]["waves"]]
    if waves[:3] != [4000, 8000, 20000] or waves[-1] != 85_727:
        raise RuntimeError("run-70 wave sizes drifted")
    if waves != sorted(set(waves)):
        raise RuntimeError("run-70 waves must be strictly increasing")
    return value


def _wave_suffix(cumulative_records: int) -> str:
    return f"{cumulative_records:05d}"


def _stratum(row: dict[str, Any], config: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(row[field]) for field in config["sampling"]["strata"])


def _stratum_id(cell: tuple[str, ...]) -> str:
    return hashlib.sha256("\x1f".join(cell).encode("utf-8")).hexdigest()[:16]


def _select_increment(
    remaining: dict[tuple[str, ...], list[dict[str, Any]]], count: int
) -> dict[tuple[str, ...], list[dict[str, Any]]]:
    populations = {cell: len(rows) for cell, rows in remaining.items() if rows}
    if count <= 0 or count > sum(populations.values()):
        raise RuntimeError("probability wave increment is infeasible")
    minima = {cell: 1 for cell in populations} if count >= len(populations) else {}
    quotas = bounded_hamilton(populations, count, minima)
    selected: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for cell in sorted(populations):
        selected[cell] = remaining[cell][: quotas[cell]]
        del remaining[cell][: quotas[cell]]
    return selected


def _cost_projection(config: dict[str, Any]) -> list[dict[str, Any]]:
    baseline = config["pricing"]["empirical_high_effort_usage_per_5000"]
    reserve = float(config["pricing"]["reserve_fraction"])
    rows: list[dict[str, Any]] = []
    for wave in config["sampling"]["waves"]:
        records = int(wave["cumulative_records"])
        scale = records / 5000
        for profile, rates in config["pricing"]["profiles"].items():
            cost = scale * (
                int(baseline["cache_hit_input_tokens"]) * float(rates["cache_hit_input"])
                + int(baseline["cache_miss_input_tokens"]) * float(rates["cache_miss_input"])
                + int(baseline["output_tokens"]) * float(rates["output"])
            ) / 1_000_000
            rows.append(
                {
                    "wave": wave["id"],
                    "cumulative_new_records": records,
                    "pricing_profile": profile,
                    "high_effort_projected_cny": round(cost, 4),
                    "high_effort_cap_with_15pct_reserve_cny": round(cost * (1 + reserve), 4),
                    "low_effort_projection": "rejected_after_run69_20_record_quality_gate",
                }
            )
    return rows


def _effective_sample_size(weights: list[float]) -> float:
    return sum(weights) ** 2 / sum(weight**2 for weight in weights)


def _worst_margin(
    full_n: int, known_n: int, remaining_n: int, effective_n: float
) -> float:
    if effective_n <= 1:
        return 1.0
    fpc = math.sqrt(max(0.0, (remaining_n - effective_n) / (remaining_n - 1)))
    remaining_margin = 1.96 * math.sqrt(0.25 / effective_n) * fpc
    return remaining_n / full_n * remaining_margin


def build() -> dict[str, Any]:
    config = load_config()
    source_path = ROOT / config["inputs"]["remaining_frame"]
    full_path = ROOT / config["inputs"]["full_population_manifest"]
    frozen_path = ROOT / config["inputs"]["frozen_v03_sample"]
    frozen_results_path = ROOT / config["inputs"]["frozen_v03_results"]
    source = read_jsonl(source_path)
    full = _read_csv(full_path)
    frozen = _read_csv(frozen_path)
    frozen_results = read_jsonl(frozen_results_path)
    population = config["population"]
    if len(source) != int(population["remaining_sampling_frame_records"]):
        raise RuntimeError("remaining 85,727-record frame count drifted")
    if len(full) != int(population["full_records"]):
        raise RuntimeError("full 90,727-record population count drifted")
    if len(frozen) != int(population["frozen_v03_known_records"]):
        raise RuntimeError("frozen 5,000-record sample count drifted")
    if len(frozen_results) != len(frozen):
        raise RuntimeError("frozen sample and result counts differ")
    source_ids = {row["record_id"] for row in source}
    frozen_ids = {row["record_id"] for row in frozen}
    full_ids = {row["record_id"] for row in full}
    if source_ids & frozen_ids or source_ids | frozen_ids != full_ids:
        raise RuntimeError("remaining frame and frozen records do not partition the population")

    seed = int(config["sampling"]["seed"])
    by_cell: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in source:
        by_cell[_stratum(row, config)].append(dict(row))
    populations = {cell: len(rows) for cell, rows in by_cell.items()}
    for cell, rows in by_cell.items():
        rows.sort(
            key=lambda row: stable_rank(
                seed, "probability", *cell, row["title_sha256"], row["record_id"]
            )
        )

    selected_rows: list[dict[str, Any]] = []
    cumulative_counts: dict[str, Counter[tuple[str, ...]]] = {}
    running: Counter[tuple[str, ...]] = Counter()
    prior = 0
    wave_order: dict[str, int] = {}
    for order, wave in enumerate(config["sampling"]["waves"], start=1):
        wave_id = str(wave["id"])
        wave_order[wave_id] = order
        cumulative = int(wave["cumulative_records"])
        increment = cumulative - prior
        selection = _select_increment(by_cell, increment)
        for cell, rows in selection.items():
            running[cell] += len(rows)
            for row in rows:
                row["probability_wave"] = wave_id
                row["probability_wave_order"] = order
                row["stratum_id"] = _stratum_id(cell)
                selected_rows.append(row)
        cumulative_counts[wave_id] = Counter(running)
        prior = cumulative
    if len(selected_rows) != int(population["maximum_new_annotations"]):
        raise RuntimeError("maximum probability sample count drifted")

    selected_rows.sort(
        key=lambda row: (
            int(row["probability_wave_order"]),
            stable_rank(seed, "dispatch", row["title_sha256"], row["record_id"]),
        )
    )
    wave_ids = [str(row["id"]) for row in config["sampling"]["waves"]]
    for sample_index, row in enumerate(selected_rows, start=1):
        cell = _stratum(row, config)
        row["sample_item_index"] = sample_index
        row["sample_batch_id"] = (
            f"probability_batch_{(sample_index - 1) // int(config['runner']['batch_size']) + 1:06d}"
        )
        for wave_id in wave_ids:
            included = int(row["probability_wave_order"]) <= wave_order[wave_id]
            sample_n = cumulative_counts[wave_id][cell]
            probability = sample_n / populations[cell] if included else 0.0
            suffix = _wave_suffix(
                int(
                    next(
                        w["cumulative_records"]
                        for w in config["sampling"]["waves"]
                        if w["id"] == wave_id
                    )
                )
            )
            row[f"inclusion_probability_{suffix}"] = probability
            row[f"analysis_weight_{suffix}"] = 1 / probability if probability else None

    PRIVATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    PRIVATE_DIR.chmod(0o700)
    atomic_jsonl(FRAME, selected_rows, 0o600)

    stratum_rows: list[dict[str, Any]] = []
    for cell in sorted(populations):
        row: dict[str, Any] = {
            "stratum_id": _stratum_id(cell),
            "population_n": populations[cell],
        }
        for wave in config["sampling"]["waves"]:
            suffix = _wave_suffix(int(wave["cumulative_records"]))
            sample_n = cumulative_counts[str(wave["id"])][cell]
            row[f"sample_n_{suffix}"] = sample_n
            row[f"inclusion_probability_{suffix}"] = sample_n / populations[cell]
            row[f"analysis_weight_{suffix}"] = populations[cell] / sample_n
        stratum_rows.append(row)
    atomic_csv(STRATUM_COUNTS, stratum_rows, list(stratum_rows[0]))

    wave_rows: list[dict[str, Any]] = []
    full_n = int(population["full_records"])
    known_n = int(population["frozen_v03_known_records"])
    remaining_n = int(population["remaining_sampling_frame_records"])
    for wave in config["sampling"]["waves"]:
        wave_id = str(wave["id"])
        weights = [
            populations[cell] / cumulative_counts[wave_id][cell]
            for cell in populations
            for _ in range(cumulative_counts[wave_id][cell])
        ]
        effective = _effective_sample_size(weights)
        zero_cells = sum(cumulative_counts[wave_id][cell] == 0 for cell in populations)
        wave_rows.append(
            {
                "wave": wave_id,
                "cumulative_new_records": int(wave["cumulative_records"]),
                "combined_detailed_records": known_n + int(wave["cumulative_records"]),
                "active_strata": len(populations),
                "zero_sample_strata": zero_cells,
                "kish_effective_sample_size_remaining": round(effective, 2),
                "worst_case_95pct_margin_full_population": round(
                    _worst_margin(full_n, known_n, remaining_n, effective), 6
                ),
                "margin_role": "planning benchmark; final inference must use design-based outcome variance",
            }
        )
    atomic_csv(WAVE_SUMMARY, wave_rows, list(wave_rows[0]))

    costs = _cost_projection(config)
    atomic_csv(COST_PROJECTION, costs, list(costs[0]))
    inputs = {
        str(path.relative_to(ROOT)): sha256(path)
        for path in (
            CONFIG,
            source_path,
            full_path,
            frozen_path,
            frozen_results_path,
            BUILDER,
        )
    }
    manifest = {
        "created_at": utc_now(),
        "status": "offline_probability_frame_prepared_production_not_authorized",
        "experiment_id": EXPECTED_EXPERIMENT_ID,
        "population_records": len(full),
        "known_frozen_records": len(frozen),
        "remaining_frame_records": len(source),
        "maximum_new_sample_records": len(selected_rows),
        "wave_counts": dict(sorted(Counter(row["probability_wave"] for row in selected_rows).items())),
        "strata": len(populations),
        "zero_sample_strata_by_wave": {
            row["wave"]: row["zero_sample_strata"] for row in wave_rows
        },
        "sampling": config["sampling"],
        "wave_summary": wave_rows,
        "cost_projection": costs,
        "inputs": inputs,
        "outputs": {
            str(path.relative_to(ROOT)): sha256(path)
            for path in (FRAME, STRATUM_COUNTS, WAVE_SUMMARY, COST_PROJECTION)
        },
        "privacy": "Titles, record IDs, source names, and unhashed stratum definitions remain private. Public outputs contain hashed stratum IDs, counts, weights, costs, and checksums only.",
        "api_calls": 0,
    }
    atomic_json(MANIFEST, manifest)
    return manifest


def main() -> None:
    manifest = build()
    print(
        json.dumps(
            {
                "population_records": manifest["population_records"],
                "remaining_frame_records": manifest["remaining_frame_records"],
                "maximum_new_sample_records": manifest["maximum_new_sample_records"],
                "wave_counts": manifest["wave_counts"],
                "strata": manifest["strata"],
                "api_calls": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
