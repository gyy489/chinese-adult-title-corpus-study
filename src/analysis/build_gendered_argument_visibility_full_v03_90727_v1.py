"""Build the offline 85,727-record exact-v0.3 production frame for run 59."""

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
)
from src.analysis.run_gendered_argument_visibility_scaleup_5000_v1 import (
    NORMALIZATION_POLICY,
)

CONFIG = ROOT / "config/gendered_argument_visibility_full_v03_90727_v1.json"
RUN_DIR = ROOT / "data/interim/59_gendered_argument_visibility_full_v03_90727_v1"
PRIVATE_DIR = RUN_DIR / "private"
FRAME = PRIVATE_DIR / "annotation_frame.jsonl"
FRAME_COUNTS = RUN_DIR / "frame_counts.csv"
COST_PROJECTION = RUN_DIR / "cost_projection.csv"
MANIFEST = RUN_DIR / "frame_manifest.json"
BUILDER = ROOT / "src/analysis/build_gendered_argument_visibility_full_v03_90727_v1.py"
RUNNER = ROOT / "src/analysis/run_gendered_argument_visibility_full_v03_90727_v1.py"
EXPECTED_EXPERIMENT_ID = "59_gendered_argument_visibility_full_v03_90727_v1"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def load_config() -> dict[str, Any]:
    value = json.loads(CONFIG.read_text(encoding="utf-8"))
    if value.get("experiment_id") != EXPECTED_EXPERIMENT_ID:
        raise RuntimeError("unexpected run-59 experiment id")
    if value.get("approval", {}).get("provider_calls_authorized") is not False:
        raise RuntimeError("offline build requires provider calls disabled in run config")
    return value


def _wave(index: int) -> str:
    if index <= 1_000:
        return "v03_gate_01000"
    if index <= 10_000:
        return "v03_ramp_10000"
    if index <= 40_000:
        return "v03_ramp_40000"
    return "v03_full_85727"


def _cost_rows(config: dict[str, Any]) -> list[dict[str, Any]]:
    usage = config["empirical_usage_baseline"]
    scale = int(config["scope"]["new_v03_records"]) / int(usage["source_records"])
    output: list[dict[str, Any]] = []
    for profile, values in config["pricing"]["profiles"].items():
        rates = values["deepseek-v4-flash"]
        expected = scale * (
            int(usage["cache_hit_input_tokens"]) * float(rates["cache_hit_input"])
            + int(usage["cache_miss_input_tokens"]) * float(rates["cache_miss_input"])
            + int(usage["output_tokens"]) * float(rates["output"])
        ) / 1_000_000
        output.append(
            {
                "pricing_profile": profile,
                "records": int(config["scope"]["new_v03_records"]),
                "expected_cost_usd": round(expected, 6),
                "recommended_cap_with_15pct_reserve_usd": round(expected * 1.15, 6),
                "basis": "linear projection from sealed 5,000-record v0.3 usage",
            }
        )
    return output


def build() -> dict[str, Any]:
    config = load_config()
    contract = validate_contract_files()
    full_path = ROOT / config["inputs"]["full_population_manifest"]
    source_path = ROOT / config["inputs"]["remaining_frame"]
    legacy_sample_path = ROOT / config["inputs"]["legacy_v03_sample"]
    legacy_results_path = ROOT / config["inputs"]["legacy_v03_results"]
    full = _read_csv(full_path)
    source = read_jsonl(source_path)
    legacy = _read_csv(legacy_sample_path)
    legacy_results = read_jsonl(legacy_results_path)
    scope = config["scope"]
    if len(full) != int(scope["full_corpus_records"]):
        raise RuntimeError("full 90,727-record manifest count drifted")
    if len(source) != int(scope["new_v03_records"]):
        raise RuntimeError("remaining 85,727-record frame count drifted")
    if len(legacy) != int(scope["reuse_frozen_v0_3_records"]) or len(legacy_results) != len(legacy):
        raise RuntimeError("legacy v0.3 layer count drifted")
    if [int(row["production_item_index"]) for row in source] != list(range(1, len(source) + 1)):
        raise RuntimeError("production indices are not contiguous")
    full_ids = {row["record_id"] for row in full}
    source_ids = {row["record_id"] for row in source}
    legacy_ids = {row["record_id"] for row in legacy}
    if source_ids & legacy_ids or source_ids | legacy_ids != full_ids:
        raise RuntimeError("new and reused v0.3 frames do not partition the full population")

    rows: list[dict[str, Any]] = []
    for original in source:
        row = dict(original)
        index = int(row["production_item_index"])
        row["router_wave"] = _wave(index)
        row["annotation_wave"] = row["router_wave"]
        rows.append(row)
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    PRIVATE_DIR.chmod(0o700)
    atomic_jsonl(FRAME, rows, 0o600)
    counts = Counter((row["annotation_wave"], row["frozen_split"]) for row in rows)
    atomic_csv(
        FRAME_COUNTS,
        [
            {"annotation_wave": wave, "frozen_split": split, "records": count}
            for (wave, split), count in sorted(counts.items())
        ],
        ["annotation_wave", "frozen_split", "records"],
    )
    costs = _cost_rows(config)
    atomic_csv(
        COST_PROJECTION,
        costs,
        [
            "pricing_profile",
            "records",
            "expected_cost_usd",
            "recommended_cap_with_15pct_reserve_usd",
            "basis",
        ],
    )
    inputs = {
        str(path.relative_to(ROOT)): sha256(path)
        for path in (
            CONFIG,
            full_path,
            source_path,
            legacy_sample_path,
            legacy_results_path,
            DEFAULT_CODEBOOK,
            DEFAULT_SCHEMA,
            DEFAULT_PROMPT,
            NORMALIZATION_POLICY,
            BUILDER,
            RUNNER,
        )
        if path.exists()
    }
    wave_counts = Counter(row["annotation_wave"] for row in rows)
    manifest = {
        "created_at": utc_now(),
        "status": "prepared_not_authorized",
        "experiment_id": config["experiment_id"],
        "full_corpus_records": len(full),
        "legacy_v03_reused_records": len(legacy),
        "new_v03_records": len(rows),
        "wave_counts": dict(sorted(wave_counts.items())),
        "measurement": {
            "codebook_version": contract["codebook"]["version"],
            "exact_frozen_v03_contract": True,
            "thinking": config["model_configs"]["annotation"]["thinking"],
            "normalizer": "argument_visibility_normalizer_v3_0",
            "router_used": False,
        },
        "failed_candidates_not_selected": [
            "router_v1",
            "router_v2",
            "reduced_17_field_nonthinking",
            "exact_v03_nonthinking",
        ],
        "cost_projection": costs,
        "inputs": inputs,
        "outputs": {
            str(FRAME.relative_to(ROOT)): sha256(FRAME),
            str(FRAME_COUNTS.relative_to(ROOT)): sha256(FRAME_COUNTS),
            str(COST_PROJECTION.relative_to(ROOT)): sha256(COST_PROJECTION),
        },
        "privacy": "Titles and record IDs remain private; public outputs contain counts and hashes only.",
        "api_calls": 0,
    }
    atomic_json(MANIFEST, manifest)
    return manifest


def main() -> None:
    result = build()
    print(
        json.dumps(
            {
                "full_corpus_records": result["full_corpus_records"],
                "legacy_v03_reused_records": result["legacy_v03_reused_records"],
                "new_v03_records": result["new_v03_records"],
                "wave_counts": result["wave_counts"],
                "api_calls": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
