"""Stable-hash sampling primitives used by the two-track design.

The implementation is independent of input row order. Hamilton allocation
assigns proportional integer quotas; a salted SHA-256 rank selects records
within cells. A disjoint mechanism-enriched component can then be appended
without describing it as probability sampled.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from typing import Any


class SamplingError(ValueError):
    """Raised when a requested design cannot be satisfied."""


def stable_rank(seed: str | int, *parts: object) -> str:
    material = "\x1f".join([str(seed), *(str(part) for part in parts)])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _cell_key(row: dict[str, Any], fields: Sequence[str]) -> tuple[str, ...]:
    return tuple(str(row.get(field, "")) for field in fields)


def hamilton_quotas(
    cell_sizes: dict[tuple[str, ...], int], total: int
) -> dict[tuple[str, ...], int]:
    """Allocate `total` proportionally with the largest-remainder method."""

    population = sum(cell_sizes.values())
    if total < 0 or total > population:
        raise SamplingError(f"sample size {total} is outside 0..{population}")
    if population == 0:
        return {}
    exact = {cell: total * size / population for cell, size in cell_sizes.items()}
    quotas = {cell: math.floor(value) for cell, value in exact.items()}
    remainder = total - sum(quotas.values())
    order = sorted(
        cell_sizes,
        key=lambda cell: (-(exact[cell] - quotas[cell]), cell),
    )
    for cell in order[:remainder]:
        quotas[cell] += 1
    return quotas


def stratified_sample(
    records: Iterable[dict[str, Any]],
    *,
    sample_size: int,
    strata: Sequence[str],
    seed: str | int,
    id_field: str = "record_id",
) -> list[dict[str, Any]]:
    """Select a deterministic, proportional sample without replacement."""

    cells: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    seen_ids: set[str] = set()
    for row in records:
        record_id = str(row.get(id_field, ""))
        if not record_id or record_id in seen_ids:
            raise SamplingError(f"missing or duplicate {id_field}: {record_id!r}")
        seen_ids.add(record_id)
        cells[_cell_key(row, strata)].append(row)
    quotas = hamilton_quotas({key: len(rows) for key, rows in cells.items()}, sample_size)
    selected: list[dict[str, Any]] = []
    for cell in sorted(cells):
        ranked = sorted(
            cells[cell],
            key=lambda row: (stable_rank(seed, cell, row[id_field]), str(row[id_field])),
        )
        for row in ranked[: quotas[cell]]:
            selected.append({**row, "sampling_track": "probability"})
    return sorted(selected, key=lambda row: stable_rank(seed, "output", row[id_field]))


def mechanism_enriched_sample(
    records: Iterable[dict[str, Any]],
    *,
    group_field: str,
    group_quotas: dict[str, int],
    seed: str | int,
    excluded_ids: Iterable[str] = (),
    id_field: str = "record_id",
) -> list[dict[str, Any]]:
    """Select pre-specified mechanism groups and label the nonprobability track."""

    excluded = {str(value) for value in excluded_ids}
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        if str(row[id_field]) not in excluded:
            groups[str(row.get(group_field, ""))].append(row)
    selected: list[dict[str, Any]] = []
    for group, quota in sorted(group_quotas.items()):
        candidates = sorted(
            groups.get(group, []),
            key=lambda row: stable_rank(seed, group, row[id_field]),
        )
        if len(candidates) < quota:
            raise SamplingError(
                f"mechanism group {group!r} has {len(candidates)} rows for quota {quota}"
            )
        selected.extend(
            {**row, "sampling_track": "mechanism_enriched"}
            for row in candidates[:quota]
        )
    return sorted(selected, key=lambda row: stable_rank(seed, "output", row[id_field]))


def design_summary(
    selected: Iterable[dict[str, Any]], *, strata: Sequence[str]
) -> dict[str, Any]:
    rows = list(selected)
    tracks = Counter(str(row["sampling_track"]) for row in rows)
    cells = Counter(_cell_key(row, strata) for row in rows)
    return {
        "selected_n": len(rows),
        "tracks": dict(sorted(tracks.items())),
        "strata": {" | ".join(key): count for key, count in sorted(cells.items())},
    }
