"""Build run 58: exact-v0.3, thinking-disabled equivalence data.

The source sample was frozen before any run-58 output exists.  Titles and
machine references remain private.  This builder performs no provider calls.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
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
from src.analysis.gendered_argument_visibility_contract_v0_3 import (
    DEFAULT_CODEBOOK,
    DEFAULT_PROMPT,
    DEFAULT_SCHEMA,
    validate_contract_files,
    validate_output,
)

CONFIG = ROOT / "config/gendered_argument_visibility_v03_nonthinking_equivalence_v1.json"
SOURCE_DIR = ROOT / "data/interim/52_gendered_argument_visibility_model_benchmark_v1"
SOURCE_ITEMS = SOURCE_DIR / "private/benchmark_items.jsonl"
SOURCE_MANIFEST = SOURCE_DIR / "sample_manifest.json"
V03_DIR = ROOT / "data/interim/51_gendered_argument_visibility_scaleup_5000_v1"
V03_SAMPLE = V03_DIR / "private/sample_manifest.csv"
V03_FINAL = V03_DIR / "private/validation_5000_final_records.jsonl"
RUN_DIR = ROOT / "data/interim/58_gendered_argument_visibility_v03_nonthinking_equivalence_v1"
PRIVATE_ITEMS = RUN_DIR / "private/benchmark_items.jsonl"
PUBLIC_COUNTS = RUN_DIR / "sample_counts.csv"
PUBLIC_MANIFEST = RUN_DIR / "sample_manifest.json"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def build() -> dict[str, Any]:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    contract = validate_contract_files()
    source = read_jsonl(SOURCE_ITEMS)
    if len(source) != int(config["records"]):
        raise RuntimeError("run-52 benchmark size drifted")
    if [int(row["benchmark_item_index"]) for row in source] != list(range(1, 101)):
        raise RuntimeError("run-52 benchmark indices are not contiguous 1..100")
    excluded = set(config["source"]["exclude_frozen_splits"])
    if any(row.get("frozen_split") in excluded for row in source):
        raise RuntimeError("protected final-blind record reached run 58")

    sample_by_index = {int(row["item_index"]): row for row in _read_csv(V03_SAMPLE)}
    final_by_index = {int(row["item_index"]): row for row in read_jsonl(V03_FINAL)}
    output_rows: list[dict[str, Any]] = []
    for source_row in source:
        source_index = int(source_row["source_item_index"])
        sample_row = sample_by_index[source_index]
        final_row = final_by_index[source_index]
        title = source_row["deidentified_title"]
        if sample_row["title_sha256"] != source_row["title_sha256"]:
            raise RuntimeError("source/v0.3 title hash mismatch")
        reference = final_row.get("output")
        if not final_row.get("valid") or not isinstance(reference, dict):
            raise RuntimeError("run 58 requires a structurally valid v0.3 reference")
        derived = validate_output(reference, title, contract["codebook"])
        output_rows.append(
            {
                "benchmark_item_index": int(source_row["benchmark_item_index"]),
                "partition": source_row["partition"],
                "benchmark_stratum": source_row["benchmark_stratum"],
                "source_item_index": source_index,
                "record_id": source_row["record_id"],
                "cluster_id": source_row["cluster_id"],
                "source_site": source_row["source_site"],
                "frozen_split": source_row["frozen_split"],
                "sampling_track": source_row["sampling_track"],
                "sampling_stratum": source_row["sampling_stratum"],
                "incremental_wave": source_row["incremental_wave"],
                "semantic_length": source_row["semantic_length"],
                "length_band": source_row["length_band"],
                "title_sha256": source_row["title_sha256"],
                "deidentified_title": title,
                "reference_output": reference,
                "reference_diagnostics": {
                    "evidence_complete": bool(derived.get("evidence_complete")),
                    "normalization_actions": final_row.get("normalization_actions", []),
                    "needs_adjudication": bool(reference.get("needs_adjudication")),
                },
            }
        )

    if len({row["cluster_id"] for row in output_rows}) != len(output_rows):
        raise RuntimeError("run 58 must contain unique expression clusters")
    PRIVATE_ITEMS.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    PRIVATE_ITEMS.parent.chmod(0o700)
    atomic_jsonl(PRIVATE_ITEMS, output_rows, 0o600)
    counts = Counter((row["partition"], row["benchmark_stratum"]) for row in output_rows)
    atomic_csv(
        PUBLIC_COUNTS,
        [
            {"partition": partition, "benchmark_stratum": stratum, "records": count}
            for (partition, stratum), count in sorted(counts.items())
        ],
        ["partition", "benchmark_stratum", "records"],
    )
    builder = ROOT / "src/analysis/build_gendered_argument_visibility_v03_nonthinking_equivalence_v1.py"
    inputs = {
        str(path.relative_to(ROOT)): sha256(path)
        for path in (
            CONFIG,
            SOURCE_ITEMS,
            SOURCE_MANIFEST,
            V03_SAMPLE,
            V03_FINAL,
            DEFAULT_CODEBOOK,
            DEFAULT_SCHEMA,
            DEFAULT_PROMPT,
            builder,
        )
    }
    manifest = {
        "created_at": utc_now(),
        "status": "prepared_not_run",
        "experiment_id": config["experiment_id"],
        "records": len(output_rows),
        "smoke_records": int(config["smoke_records"]),
        "partitions": dict(Counter(row["partition"] for row in output_rows)),
        "reference_role": config["reference_role"],
        "single_variable": config["single_variable"],
        "contract": {
            "codebook_version": contract["codebook"]["version"],
            "codebook_sha256": sha256(DEFAULT_CODEBOOK),
            "schema_sha256": sha256(DEFAULT_SCHEMA),
            "prompt_sha256": sha256(DEFAULT_PROMPT),
            "identical_to_frozen_v0.3": True,
        },
        "selection": {
            "source": "exact run-52 benchmark order; no run-58 outcome-based resampling",
            "unique_expression_clusters": len(output_rows),
            "excluded_frozen_splits": sorted(excluded),
            "excluded_split_selected_records": 0,
        },
        "run_plan": {
            "first_gate_records": int(config["smoke_records"]),
            "batch_size": int(config["model"]["batch_size"]),
            "workers": int(config["model"]["workers"]),
            "thinking": config["model"]["thinking"],
            "api_calls_at_build": 0,
        },
        "inputs": inputs,
        "outputs": {
            str(PRIVATE_ITEMS.relative_to(ROOT)): sha256(PRIVATE_ITEMS),
            str(PUBLIC_COUNTS.relative_to(ROOT)): sha256(PUBLIC_COUNTS),
        },
        "privacy": "Titles, record IDs, references, and evidence remain private.",
        "api_calls": 0,
    }
    atomic_json(PUBLIC_MANIFEST, manifest)
    return manifest


def main() -> None:
    result = build()
    print(
        json.dumps(
            {
                "records": result["records"],
                "smoke_records": result["smoke_records"],
                "contract_identical": result["contract"]["identical_to_frozen_v0.3"],
                "api_calls": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
