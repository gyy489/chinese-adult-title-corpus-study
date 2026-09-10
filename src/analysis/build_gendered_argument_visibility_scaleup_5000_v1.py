"""Freeze the two-track 5,000-row v0.3 argument-visibility sample."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
from collections import Counter, defaultdict
from collections.abc import Hashable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config/gendered_argument_visibility_scaleup_5000_v1.json"
CLUSTERS = ROOT / "data/interim/40_corpus_audit_v1/cluster_split_assignments_private.csv"
SOURCE = ROOT / "data/interim/43_deepseek_v4_flash_full_run/private/full_manifest.csv"
FINAL_V1 = ROOT / "data/interim/44_deepseek_normalized_v1/private/final_records.jsonl"
SIGNALS = ROOT / "data/interim/46_production_mode_pilot_v1/private/record_candidate_signals.csv"
BLIND45 = ROOT / "data/interim/45_human_blind_review_v1/private/blind_queue.csv"
PILOT47 = ROOT / "data/interim/47_gendered_agency_pilot_v1/private/pilot_manifest.csv"
PILOT48 = ROOT / "data/interim/48_gendered_argument_visibility_pilot_v1/private/pilot_manifest.csv"
RUN_DIR = ROOT / "data/interim/51_gendered_argument_visibility_scaleup_5000_v1"
PRIVATE = RUN_DIR / "private"
SAMPLE = PRIVATE / "sample_manifest.csv"
CANDIDATES = PRIVATE / "candidate_order.csv"
CELL_COUNTS_PRIVATE = PRIVATE / "cell_counts.csv"
PUBLIC_COUNTS = RUN_DIR / "sample_counts.csv"
MANIFEST = RUN_DIR / "sample_manifest.json"

FROZEN_SPLITS = (
    "codebook_development",
    "model_training",
    "stability_validation",
    "final_blind_test",
)
MECHANISM_STRATA = (
    "privacy_only",
    "privacy_and_force",
    "privacy_and_direct",
    "force_only",
    "direct_only",
)
CONTROL = {
    "physical_aggression_pain",
    "restraint_confinement",
    "threat_economic_authority",
    "domination_discipline",
}
WAVES = ("gate_0500", "discovery_1500", "validation_3000")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_rank(seed: int, *parts: str) -> str:
    value = "\x1f".join((str(seed), *parts)).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str], mode: int) -> None:
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


def write_json(path: Path, value: Any, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def bounded_hamilton(
    populations: dict[Hashable, int],
    total: int,
    minima: dict[Hashable, int] | None = None,
) -> dict[Hashable, int]:
    """Allocate an integer total proportionally subject to minima and capacities."""
    minima = minima or {}
    keys = sorted(populations, key=str)
    allocation = {key: int(minima.get(key, 0)) for key in keys}
    if any(allocation[key] > populations[key] for key in keys):
        raise RuntimeError("allocation minimum exceeds cell population")
    remaining = total - sum(allocation.values())
    if remaining < 0 or total > sum(populations.values()):
        raise RuntimeError("requested allocation is infeasible")
    while remaining:
        capacities = {key: populations[key] - allocation[key] for key in keys}
        active = [key for key in keys if capacities[key] > 0]
        if not active:
            raise RuntimeError("allocation exhausted before reaching total")
        capacity_total = sum(capacities[key] for key in active)
        ideals = {
            key: remaining * capacities[key] / capacity_total for key in active
        }
        floors = {
            key: min(capacities[key], int(ideals[key])) for key in active
        }
        floor_sum = sum(floors.values())
        if floor_sum:
            for key, amount in floors.items():
                allocation[key] += amount
            remaining -= floor_sum
            continue
        order = sorted(
            active,
            key=lambda key: (-(ideals[key] - int(ideals[key])), str(key)),
        )
        for key in order[:remaining]:
            allocation[key] += 1
        remaining = 0
    return allocation


def representative_rows(
    cluster_rows: list[dict[str, Any]], seed: int
) -> list[dict[str, Any]]:
    by_cluster: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in cluster_rows:
        by_cluster[row["cluster_id"]].append(row)
    representatives = []
    for cluster_id, rows in by_cluster.items():
        ranked = sorted(
            rows,
            key=lambda row: stable_rank(seed, cluster_id, row["record_id"]),
        )
        representative = dict(ranked[0])
        representative["representative_rank"] = 1
        representative["eligible_cluster_records"] = len(ranked)
        representatives.append(representative)
    return representatives


def allocate_probability(
    rows: list[dict[str, Any]], quota: int, seed: int, split: str
) -> tuple[list[dict[str, Any]], dict[tuple[str, str], tuple[int, int]]]:
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_source[row["source_site"]].append(row)
    source_pop = {source: len(values) for source, values in by_source.items()}
    source_min = {
        source: len({row["length_band"] for row in values})
        for source, values in by_source.items()
    }
    source_quota = bounded_hamilton(source_pop, quota, source_min)
    selected: list[dict[str, Any]] = []
    cell_stats: dict[tuple[str, str], tuple[int, int]] = {}
    for source, values in sorted(by_source.items()):
        by_band: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in values:
            by_band[row["length_band"]].append(row)
        band_pop = {band: len(items) for band, items in by_band.items()}
        band_quota = bounded_hamilton(
            band_pop,
            source_quota[source],
            {band: 1 for band in band_pop},
        )
        for band, candidates in sorted(by_band.items()):
            ranked = sorted(
                candidates,
                key=lambda row: stable_rank(
                    seed, "P", split, source, band, row["cluster_id"]
                ),
            )
            n = band_quota[band]
            for order, row in enumerate(ranked, 1):
                row["probability_candidate_order"] = order
                row["probability_selected"] = order <= n
            selected.extend(ranked[:n])
            cell_stats[(source, band)] = (len(ranked), n)
    if len(selected) != quota:
        raise RuntimeError(f"probability track {split}: selected {len(selected)}, need {quota}")
    return selected, cell_stats


def mechanism_stratum(row: dict[str, Any]) -> str | None:
    record = row.get("v1_record") or {}
    if not record.get("valid") or not isinstance(record.get("output"), dict):
        return None
    output = record["output"]
    harm = set(output.get("harm_script_codes", []))
    privacy = (
        "privacy_surveillance_exposure" in harm
        or row.get("acquisition_distribution_candidate") == "yes"
    )
    force = "force_nonconsent" in harm
    direct = not force and bool(harm & CONTROL)
    row["aux_privacy"] = privacy
    row["aux_force"] = force
    row["aux_direct"] = direct
    if privacy and force:
        return "privacy_and_force"
    if privacy and direct:
        return "privacy_and_direct"
    if privacy:
        return "privacy_only"
    if force:
        return "force_only"
    if direct:
        return "direct_only"
    return None


def allocate_mechanism(
    rows: list[dict[str, Any]], quota: int, seed: int, split: str, stratum: str
) -> tuple[list[dict[str, Any]], dict[tuple[str, str], tuple[int, int]]]:
    by_cell: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_cell[(row["source_site"], row["length_band"])].append(row)
    populations = {cell: len(values) for cell, values in by_cell.items()}
    allocation = bounded_hamilton(populations, quota)
    selected: list[dict[str, Any]] = []
    cell_stats: dict[tuple[str, str], tuple[int, int]] = {}
    for cell, candidates in sorted(by_cell.items()):
        ranked = sorted(
            candidates,
            key=lambda row: stable_rank(
                seed, "M", split, stratum, *cell, row["cluster_id"]
            ),
        )
        n = allocation[cell]
        for order, row in enumerate(ranked, 1):
            row["mechanism_candidate_order"] = order
            row["mechanism_selected"] = order <= n
        selected.extend(ranked[:n])
        cell_stats[cell] = (len(ranked), n)
    if len(selected) != quota:
        raise RuntimeError(
            f"mechanism track {split}/{stratum}: selected {len(selected)}, need {quota}"
        )
    return selected, cell_stats


def assign_waves(
    selected_p: list[dict[str, Any]],
    selected_m: list[dict[str, Any]],
    config: dict[str, Any],
) -> None:
    seed = int(config["seed_wave_assignment"])
    fractions = {"gate_0500": 0.1, "discovery_1500": 0.3, "validation_3000": 0.6}
    for split, split_total in config["probability_track_by_split"].items():
        pool = [row for row in selected_p if row["frozen_split"] == split]
        remaining = list(pool)
        for wave in WAVES:
            need = round(int(split_total) * fractions[wave])
            by_cell: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
            for row in remaining:
                by_cell[(row["source_site"], row["length_band"])].append(row)
            allocation = bounded_hamilton(
                {cell: len(rows) for cell, rows in by_cell.items()}, need
            )
            chosen_ids: set[str] = set()
            for cell, rows in sorted(by_cell.items()):
                ranked = sorted(
                    rows,
                    key=lambda row: stable_rank(
                        seed, "P-WAVE", wave, split, *cell, row["cluster_id"]
                    ),
                )
                for row in ranked[: allocation[cell]]:
                    row["incremental_wave"] = wave
                    chosen_ids.add(row["cluster_id"])
            remaining = [row for row in remaining if row["cluster_id"] not in chosen_ids]
        if remaining:
            raise RuntimeError(f"unassigned probability rows in {split}: {len(remaining)}")
    wave_needs = config["mechanism_wave_strata"]
    for stratum in MECHANISM_STRATA:
        remaining = [row for row in selected_m if row["sampling_stratum"] == stratum]
        for wave in WAVES:
            need = int(wave_needs[wave][stratum])
            by_cell: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
            for row in remaining:
                by_cell[(row["frozen_split"], row["source_site"], row["length_band"])].append(row)
            allocation = bounded_hamilton(
                {cell: len(rows) for cell, rows in by_cell.items()}, need
            )
            chosen_ids: set[str] = set()
            for cell, rows in sorted(by_cell.items()):
                ranked = sorted(
                    rows,
                    key=lambda row: stable_rank(
                        seed, "M-WAVE", wave, stratum, *cell, row["cluster_id"]
                    ),
                )
                for row in ranked[: allocation[cell]]:
                    row["incremental_wave"] = wave
                    chosen_ids.add(row["cluster_id"])
            remaining = [row for row in remaining if row["cluster_id"] not in chosen_ids]
        if remaining:
            raise RuntimeError(f"unassigned mechanism rows in {stratum}: {len(remaining)}")


def main() -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    cluster_meta = {row["record_id"]: row for row in read_csv(CLUSTERS)}
    source_rows = read_csv(SOURCE)
    source_by_record = {row["record_id"]: row for row in source_rows}
    signal_by_record = {row["record_id"]: row for row in read_csv(SIGNALS)}
    final_by_index = {int(row["item_index"]): row for row in read_jsonl(FINAL_V1)}
    exposed: set[str] = set()
    for path in (BLIND45, PILOT47, PILOT48):
        exposed.update(row["cluster_id"] for row in read_csv(path))
    eligible_records: list[dict[str, Any]] = []
    for record_id, source in source_by_record.items():
        meta = cluster_meta[record_id]
        if meta["frozen_split"] not in FROZEN_SPLITS:
            continue
        signal = signal_by_record.get(record_id, {})
        eligible_records.append(
            {
                "record_id": record_id,
                "old_item_index": int(source["item_index"]),
                "deidentified_title": source["deidentified_title"],
                "title_sha256": source["title_sha256"],
                "source_site": meta["source_site"],
                "data_round": meta["data_round"],
                "frozen_split": meta["frozen_split"],
                "cluster_id": meta["cluster_id"],
                "cluster_size": int(meta["cluster_size"]),
                "length_band": meta["length_band"],
                "semantic_length": int(meta["semantic_length"]),
                "acquisition_distribution_candidate": signal.get(
                    "acquisition_distribution_candidate", "no"
                ),
                "v1_record": final_by_index.get(int(source["item_index"])),
            }
        )
    representatives = representative_rows(
        eligible_records, int(config["seed_cluster_representative"])
    )
    selected_p: list[dict[str, Any]] = []
    probability_cells: list[dict[str, Any]] = []
    for split in FROZEN_SPLITS:
        pool = [row for row in representatives if row["frozen_split"] == split]
        chosen, stats = allocate_probability(
            pool,
            int(config["probability_track_by_split"][split]),
            int(config["seed_probability_track"]),
            split,
        )
        for row in chosen:
            row["sampling_track"] = "probability"
            row["sampling_stratum"] = "corpus_probability"
            n_h, sample_h = stats[(row["source_site"], row["length_band"])]
            row["population_clusters_h"] = n_h
            row["sampled_clusters_h"] = sample_h
            row["pi_cluster"] = sample_h / n_h
            row["pi_record"] = row["pi_cluster"] / row["cluster_size"]
            row["w_cluster"] = n_h / sample_h
            row["w_record"] = row["cluster_size"] * n_h / sample_h
        selected_p.extend(chosen)
        for (source, band), (population, selected) in stats.items():
            probability_cells.append(
                {
                    "sampling_track": "probability",
                    "frozen_split": split,
                    "sampling_stratum": "corpus_probability",
                    "source_site": source,
                    "length_band": band,
                    "available_clusters": population,
                    "selected_clusters": selected,
                }
            )
    selected_p_clusters = {row["cluster_id"] for row in selected_p}
    mechanism_pools: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in representatives:
        if row["cluster_id"] in selected_p_clusters or row["cluster_id"] in exposed:
            continue
        stratum = mechanism_stratum(row)
        if stratum:
            mechanism_pools[(row["frozen_split"], stratum)].append(row)
    selected_m: list[dict[str, Any]] = []
    mechanism_cells: list[dict[str, Any]] = []
    for split in FROZEN_SPLITS:
        for stratum in MECHANISM_STRATA:
            quota = int(config["mechanism_track_by_split_and_stratum"][split][stratum])
            chosen, stats = allocate_mechanism(
                mechanism_pools[(split, stratum)],
                quota,
                int(config["seed_mechanism_track"]),
                split,
                stratum,
            )
            for row in chosen:
                row["sampling_track"] = "mechanism"
                row["sampling_stratum"] = stratum
                n_h, sample_h = stats[(row["source_site"], row["length_band"])]
                row["population_clusters_h"] = n_h
                row["sampled_clusters_h"] = sample_h
                row["pi_cluster"] = sample_h / n_h
                row["pi_record"] = row["pi_cluster"] / row["cluster_size"]
                row["w_cluster"] = n_h / sample_h
                row["w_record"] = row["cluster_size"] * n_h / sample_h
            selected_m.extend(chosen)
            for (source, band), (population, selected) in stats.items():
                mechanism_cells.append(
                    {
                        "sampling_track": "mechanism",
                        "frozen_split": split,
                        "sampling_stratum": stratum,
                        "source_site": source,
                        "length_band": band,
                        "available_clusters": population,
                        "selected_clusters": selected,
                    }
                )
    assign_waves(selected_p, selected_m, config)
    selected = selected_p + selected_m
    if len(selected) != 5000 or len({row["cluster_id"] for row in selected}) != 5000:
        raise RuntimeError("sample must contain exactly 5,000 unique expression clusters")
    wave_rank = {wave: index for index, wave in enumerate(WAVES)}
    selected.sort(
        key=lambda row: (
            wave_rank[row["incremental_wave"]],
            stable_rank(
                int(config["seed_wave_assignment"]),
                "FINAL-ORDER",
                row["incremental_wave"],
                row["sampling_track"],
                row["sampling_stratum"],
                row["cluster_id"],
            ),
        )
    )
    cumulative_limit = {"gate_0500": 500, "discovery_1500": 2000, "validation_3000": 5000}
    sample_rows: list[dict[str, Any]] = []
    for item_index, row in enumerate(selected, 1):
        sample_rows.append(
            {
                "item_index": item_index,
                "batch_id": f"batch_{((item_index - 1) // int(config['batch_size'])) + 1:06d}",
                "incremental_wave": row["incremental_wave"],
                "cumulative_wave_limit": cumulative_limit[row["incremental_wave"]],
                "sampling_track": row["sampling_track"],
                "sampling_stratum": row["sampling_stratum"],
                "record_id": row["record_id"],
                "old_item_index": row["old_item_index"],
                "title_sha256": row["title_sha256"],
                "deidentified_title": row["deidentified_title"],
                "source_site": row["source_site"],
                "data_round": row["data_round"],
                "frozen_split": row["frozen_split"],
                "cluster_id": row["cluster_id"],
                "cluster_size": row["cluster_size"],
                "semantic_length": row["semantic_length"],
                "length_band": row["length_band"],
                "population_clusters_h": row["population_clusters_h"],
                "sampled_clusters_h": row["sampled_clusters_h"],
                "pi_cluster": f"{row['pi_cluster']:.12g}",
                "pi_record": f"{row['pi_record']:.12g}",
                "w_cluster": f"{row['w_cluster']:.12g}",
                "w_record": f"{row['w_record']:.12g}",
                "aux_privacy": "yes" if row.get("aux_privacy") else "no",
                "aux_force": "yes" if row.get("aux_force") else "no",
                "aux_direct": "yes" if row.get("aux_direct") else "no",
                "prior_exposed_cluster": "yes" if row["cluster_id"] in exposed else "no",
            }
        )
    sample_fields = list(sample_rows[0])
    write_csv(SAMPLE, sample_rows, sample_fields, 0o600)
    candidate_rows: list[dict[str, Any]] = []
    for row in representatives:
        candidate_rows.append(
            {
                "sampling_track": "probability",
                "frozen_split": row["frozen_split"],
                "sampling_stratum": "corpus_probability",
                "source_site": row["source_site"],
                "length_band": row["length_band"],
                "cluster_id": row["cluster_id"],
                "record_id": row["record_id"],
                "candidate_order": row.get("probability_candidate_order", ""),
                "selected": "yes" if row["cluster_id"] in selected_p_clusters else "no",
                "exclusion_reason": "",
            }
        )
    selected_m_clusters = {row["cluster_id"] for row in selected_m}
    for (split, stratum), pool in sorted(mechanism_pools.items()):
        for row in pool:
            candidate_rows.append(
                {
                    "sampling_track": "mechanism",
                    "frozen_split": split,
                    "sampling_stratum": stratum,
                    "source_site": row["source_site"],
                    "length_band": row["length_band"],
                    "cluster_id": row["cluster_id"],
                    "record_id": row["record_id"],
                    "candidate_order": row.get("mechanism_candidate_order", ""),
                    "selected": "yes" if row["cluster_id"] in selected_m_clusters else "no",
                    "exclusion_reason": "",
                }
            )
    write_csv(CANDIDATES, candidate_rows, list(candidate_rows[0]), 0o600)
    cell_rows = probability_cells + mechanism_cells
    write_csv(CELL_COUNTS_PRIVATE, cell_rows, list(cell_rows[0]), 0o600)
    public_rows: list[dict[str, Any]] = []
    for wave in WAVES:
        for track in ("probability", "mechanism"):
            public_rows.append(
                {
                    "dimension": "wave_track",
                    "track": track,
                    "level": wave,
                    "selected_records": sum(
                        row["incremental_wave"] == wave and row["sampling_track"] == track
                        for row in sample_rows
                    ),
                }
            )
    for split in FROZEN_SPLITS:
        for track in ("probability", "mechanism"):
            public_rows.append(
                {
                    "dimension": "split_track",
                    "track": track,
                    "level": split,
                    "selected_records": sum(
                        row["frozen_split"] == split and row["sampling_track"] == track
                        for row in sample_rows
                    ),
                }
            )
    for stratum in MECHANISM_STRATA:
        public_rows.append(
            {
                "dimension": "mechanism_stratum",
                "track": "mechanism",
                "level": stratum,
                "selected_records": sum(row["sampling_stratum"] == stratum for row in sample_rows),
            }
        )
    for band in sorted({row["length_band"] for row in sample_rows}):
        for track in ("probability", "mechanism"):
            public_rows.append(
                {
                    "dimension": "length_track",
                    "track": track,
                    "level": band,
                    "selected_records": sum(
                        row["length_band"] == band and row["sampling_track"] == track
                        for row in sample_rows
                    ),
                }
            )
    write_csv(PUBLIC_COUNTS, public_rows, list(public_rows[0]), 0o644)
    counts = Counter((row["incremental_wave"], row["sampling_track"]) for row in sample_rows)
    manifest = {
        "created_at": utc_now(),
        "status": "frozen_not_run",
        "run_id": "gendered_argument_visibility_scaleup_5000_v1",
        "inputs": {
            str(path.relative_to(ROOT)): sha256(path)
            for path in (CONFIG, CLUSTERS, SOURCE, FINAL_V1, SIGNALS, BLIND45, PILOT47, PILOT48)
        },
        "parameters": config,
        "counts": {
            "eligible_records": len(eligible_records),
            "eligible_expression_clusters": len(representatives),
            "selected_records": len(sample_rows),
            "selected_expression_clusters": len({row["cluster_id"] for row in sample_rows}),
            "probability_track": sum(row["sampling_track"] == "probability" for row in sample_rows),
            "mechanism_track": sum(row["sampling_track"] == "mechanism" for row in sample_rows),
            "probability_mechanism_cluster_overlap": len(selected_p_clusters & selected_m_clusters),
            "mechanism_prior_exposed_overlap": sum(
                row["sampling_track"] == "mechanism" and row["prior_exposed_cluster"] == "yes"
                for row in sample_rows
            ),
            "wave_track": {f"{wave}:{track}": counts[(wave, track)] for wave in WAVES for track in ("probability", "mechanism")},
        },
        "outputs": {
            str(path.relative_to(ROOT)): sha256(path)
            for path in (SAMPLE, CANDIDATES, CELL_COUNTS_PRIVATE, PUBLIC_COUNTS)
        },
        "privacy": "Private files are mode 0600 and gitignored. Public counts contain no titles, identifiers, source names, evidence, or provider responses.",
        "interpretation": "P estimates the frozen corpus with record and cluster weights; M estimates mechanism-frame conditional configurations and is not a corpus-prevalence sample.",
    }
    write_json(MANIFEST, manifest)
    print(json.dumps(manifest["counts"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
