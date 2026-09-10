"""Integrate frozen 5,000-row v0.3 results with run 59's new annotations."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

from src.analysis.build_gendered_argument_visibility_full_v03_90727_v1 import (
    CONFIG,
    ROOT,
    RUN_DIR,
)
from src.analysis.build_gendered_argument_visibility_model_benchmark_v1 import (
    atomic_json,
    atomic_jsonl,
    read_jsonl,
    sha256,
    utc_now,
)
from src.analysis.run_gendered_argument_visibility_full_v03_90727_v1 import (
    FINAL_MANIFEST,
    PRIVATE_FINAL,
)

OUTPUT = RUN_DIR / "private/integrated_records_v03_90727_v1.jsonl"
MANIFEST = RUN_DIR / "integration_manifest.json"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def integrate() -> dict[str, object]:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    full_path = ROOT / config["inputs"]["full_population_manifest"]
    legacy_sample_path = ROOT / config["inputs"]["legacy_v03_sample"]
    legacy_results_path = ROOT / config["inputs"]["legacy_v03_results"]
    if not PRIVATE_FINAL.exists() or not FINAL_MANIFEST.exists():
        raise RuntimeError("finalize run 59 before integration")
    final_manifest = json.loads(FINAL_MANIFEST.read_text(encoding="utf-8"))
    if final_manifest.get("status") not in {"complete", "complete_with_unresolved"}:
        raise RuntimeError("run 59 is incomplete")
    full = _read_csv(full_path)
    legacy_sample = _read_csv(legacy_sample_path)
    legacy_results = {int(row["item_index"]): row for row in read_jsonl(legacy_results_path)}
    new_results = {row["record_id"]: row for row in read_jsonl(PRIVATE_FINAL)}
    legacy_by_id = {row["record_id"]: row for row in legacy_sample}
    if len(new_results) != 85_727 or len(legacy_by_id) != 5_000:
        raise RuntimeError("new/reused v0.3 counts drifted")
    if set(new_results) & set(legacy_by_id):
        raise RuntimeError("new and legacy v0.3 layers overlap")

    rows: list[dict[str, object]] = []
    counts: Counter[str] = Counter()
    for full_row in full:
        record_id = full_row["record_id"]
        if record_id in legacy_by_id:
            sample = legacy_by_id[record_id]
            record = legacy_results[int(sample["item_index"])]
            valid = bool(record.get("valid"))
            status = "complete" if valid else "unresolved"
            source = "legacy_v0.3_frozen_5000"
            output = record.get("output") if valid else None
            artifact_path = (
                f"data/interim/51_gendered_argument_visibility_scaleup_5000_v1/"
                f"private/validation_5000_final_records.jsonl#item_index={sample['item_index']}"
            )
            error = record.get("error")
        else:
            record = new_results[record_id]
            valid = bool(record.get("valid"))
            status = str(record["status"])
            source = "run59_exact_v0.3_thinking"
            output = record.get("output") if valid else None
            artifact_path = record.get("artifact_path")
            error = record.get("error")
        counts[f"{source}:{status}"] += 1
        rows.append(
            {
                "full_item_index": int(full_row["item_index"]),
                "record_id": record_id,
                "title_sha256": full_row["title_sha256"],
                "corpus_origin": full_row["corpus_origin"],
                "annotation_source": source,
                "annotation_version": "gendered_argument_visibility_v0.3.0",
                "status": status,
                "valid": valid,
                "output": output,
                "error": error,
                "artifact_path": artifact_path,
            }
        )
    if len(rows) != 90_727 or [row["full_item_index"] for row in rows] != list(range(1, 90_728)):
        raise RuntimeError("integrated layer is not the ordered 90,727-record population")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_jsonl(OUTPUT, rows, 0o600)
    manifest: dict[str, object] = {
        "created_at": utc_now(),
        "status": "complete",
        "records": len(rows),
        "annotation_version": "gendered_argument_visibility_v0.3.0",
        "source_status_counts": dict(sorted(counts.items())),
        "deepseek_full_coding_v1_replaced": False,
        "upstream_provider_calls": int(final_manifest["provider_calls"]),
        "upstream_actual_cost_usd": float(final_manifest["actual_cost_usd"]),
        "inputs": {
            str(path.relative_to(ROOT)): sha256(path)
            for path in (
                CONFIG,
                full_path,
                legacy_sample_path,
                legacy_results_path,
                PRIVATE_FINAL,
                FINAL_MANIFEST,
            )
        },
        "outputs": {str(OUTPUT.relative_to(ROOT)): sha256(OUTPUT)},
        "privacy": "Integrated output is private and contains no title text.",
        "api_calls_during_integration": 0,
    }
    atomic_json(MANIFEST, manifest)
    return manifest


def main() -> None:
    print(json.dumps(integrate()))


if __name__ == "__main__":
    main()
