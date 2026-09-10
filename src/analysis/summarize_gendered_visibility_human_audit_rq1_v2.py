"""Corrected RQ1 aggregate for the two-coder AI-accuracy audit."""

from __future__ import annotations

import csv
import json
import tempfile
from pathlib import Path
from typing import Any

from src.analysis.summarize_gendered_visibility_human_audit_400_v1 import (
    ROOT,
    sha256_file,
)
from src.analysis.summarize_gendered_visibility_human_audit_rq1_v1 import (
    build as build_v1,
)

OUTPUT_DIR = ROOT / "data/processed/gendered_visibility_human_audit_rq1_v2"
CORRECTION = (
    ROOT / "config/gendered_visibility_human_audit_identity_correction_v1.json"
)
SCRIPT = Path(__file__).resolve()
ANALYSIS_DATE = "2026-08-20"
ANALYSIS_ID = "gendered_visibility_human_audit_rq1_v2"
TABLES = (
    "rq1_estimates.csv",
    "rq1_paired_cells.csv",
    "rq1_pairwise_agreement.csv",
    "rq1_pairwise_confusion.csv",
)
SOURCE_MAP = {
    "reviewer_1_recheck_v2": "reviewer_1",
    "reviewer_2_v1": "reviewer_2",
}
PAIR_MAP = {
    "ai_vs_reviewer_1_recheck": "ai_vs_reviewer_1",
    "reviewer_1_recheck_vs_reviewer_2": "reviewer_1_vs_reviewer_2",
}
INSTRUCTION_MAP = {
    "v2.0.0_direct_guidance_recheck": (
        "v2.0.0_expanded_guidance_same_semantic_contract"
    ),
    "v1.0.0_original_guidance": "v1.0.0_base_guidance_same_semantic_contract",
}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def transform_csv(source: Path, destination: Path) -> None:
    with source.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise RuntimeError(f"empty upstream table: {source}")
    for row in rows:
        for key in ("source_id", "first_source", "second_source"):
            if key in row:
                row[key] = SOURCE_MAP.get(row[key], row[key])
        if "pair_id" in row:
            row["pair_id"] = PAIR_MAP.get(row["pair_id"], row["pair_id"])
        if "source_instruction_version" in row:
            row["source_instruction_version"] = INSTRUCTION_MAP.get(
                row["source_instruction_version"], row["source_instruction_version"]
            )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def build(output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    correction = json.loads(CORRECTION.read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="human-rq1-v2-") as temp_name:
        temp_dir = Path(temp_name)
        upstream = build_v1(temp_dir)
        upstream_provenance = json.loads(
            (temp_dir / "provenance.json").read_text(encoding="utf-8")
        )
        for name in TABLES:
            transform_csv(temp_dir / name, output_dir / name)

    source_specific_n = {
        SOURCE_MAP.get(source, source): count
        for source, count in upstream["counts"]["source_specific_eligible_n"].items()
    }
    old_profiles = upstream_provenance["reviewer_profiles"]
    provenance = {
        "analysis_date": ANALYSIS_DATE,
        "analysis_id": ANALYSIS_ID,
        "supersedes": "gendered_visibility_human_audit_rq1_v1",
        "correction": (
            "The deleted run was a researcher interface pilot. The current "
            "reviewer_1 and reviewer_2 databases belong to two distinct, "
            "first-exposure, AI-blind formal human coders."
        ),
        "audit_role": "validation_of_ai_coding_accuracy_for_the_primary_estimand",
        "automated_labels_modified": False,
        "adjudication": "none",
        "corpus": upstream_provenance["corpus"],
        "corpus_records": upstream_provenance["corpus_records"],
        "rq1_eligibility": upstream_provenance["rq1_eligibility"],
        "visible_position_set": upstream_provenance["visible_position_set"],
        "scopes": upstream_provenance["scopes"],
        "sample": {
            **upstream_provenance["sample"],
            "source_specific_eligible_n": source_specific_n,
        },
        "reviewer_profiles": {
            "reviewer_1": {
                "current_result": "independent_first_exposure_formal_review",
                "first_exposure_to_sample": True,
                "ai_blind": True,
                "distinct_from_researcher_pilot": True,
                "instruction_payload": (
                    "v2 expanded explanatory layer over the shared semantic instrument"
                ),
                "current_completed_records": 400,
                "database_logical_snapshot_sha256": old_profiles["reviewer_1"][
                    "database_logical_snapshot_sha256"
                ],
                "earliest_update": old_profiles["reviewer_1"]["earliest_update"],
                "latest_update": old_profiles["reviewer_1"]["latest_update"],
            },
            "reviewer_2": {
                "current_result": "independent_first_exposure_formal_review",
                "first_exposure_to_sample": True,
                "ai_blind": True,
                "distinct_from_researcher_pilot": True,
                "instruction_payload": "v1 base explanatory layer",
                "current_completed_records": 400,
                "database_logical_snapshot_sha256": old_profiles["reviewer_2"][
                    "database_logical_snapshot_sha256"
                ],
                "earliest_update": old_profiles["reviewer_2"]["earliest_update"],
                "latest_update": old_profiles["reviewer_2"]["latest_update"],
            },
        },
        "excluded_researcher_pilot": correction["excluded_researcher_pilot"],
        "shared_formal_audit_conditions": correction[
            "shared_formal_audit_conditions"
        ],
        "uncertainty": upstream_provenance["uncertainty"],
        "privacy": upstream_provenance["privacy"],
        "interpretation_limits": [
            "The formal human outputs validate AI coding accuracy relative to independent human reference judgments.",
            "Source-specific eligibility estimates use different coder-defined domains and are not fixed-denominator agreement statistics.",
            "The AI-reference-eligible scope is the fixed-denominator accuracy diagnostic.",
            "No adjudicated consensus labels were created and no frozen full-corpus AI label was overwritten.",
        ],
    }
    provenance_path = output_dir / "provenance.json"
    write_json(provenance_path, provenance)

    readme = """# RQ1 human-audit aggregate (v2)

This corrected aggregate evaluates the accuracy of the frozen AI coding for the
primary target--actor comparison against two independent formal human coders.
Both formal coders encountered the same fixed 400-record sample for the first
time, in coder-specific randomized orders, and did not see AI labels. The
deleted earlier run was a researcher interface pilot and contributes no labels.

The numerical estimates and agreement results are unchanged from v1; only the
incorrect coder-identity and prior-exposure description has been corrected.
Full estimates, paired cells, agreement, and confusion tables are retained here
for reproducibility, while the manuscript reports only the concise validation
result needed to support the AI-coded analysis.

Rebuild with:

```bash
scripts/evosci-python -m src.analysis.summarize_gendered_visibility_human_audit_rq1_v2
```
"""
    readme_path = output_dir / "README.md"
    readme_path.write_text(readme, encoding="utf-8")

    input_hashes = dict(upstream["inputs"])
    input_hashes[CORRECTION.relative_to(ROOT).as_posix()] = sha256_file(CORRECTION)
    input_hashes[SCRIPT.relative_to(ROOT).as_posix()] = sha256_file(SCRIPT)
    output_paths = [*(output_dir / name for name in TABLES), provenance_path, readme_path]
    manifest = {
        "analysis_date": ANALYSIS_DATE,
        "analysis_id": ANALYSIS_ID,
        "status": "complete_frozen_aggregate_correction",
        "supersedes": "gendered_visibility_human_audit_rq1_v1",
        "parameters": {
            **upstream["parameters"],
            "sources": ["frozen_ai_v0_3", "reviewer_1", "reviewer_2"],
            "pairs": [
                "ai_vs_reviewer_1",
                "ai_vs_reviewer_2",
                "reviewer_1_vs_reviewer_2",
            ],
            "researcher_pilot_included": False,
            "formal_coders_first_exposure": True,
            "formal_coders_ai_blind": True,
            "accuracy_reference": "independent_human_coding",
        },
        "counts": {
            **upstream["counts"],
            "reviewer_1_records": 400,
            "reviewer_2_records": 400,
            "researcher_pilot_records_included": 0,
            "source_specific_eligible_n": source_specific_n,
        },
        "inputs": input_hashes,
        "private_database_logical_snapshots": upstream[
            "private_database_logical_snapshots"
        ],
        "outputs": {path.name: sha256_file(path) for path in output_paths},
    }
    manifest["counts"].pop("reviewer_1_recheck_records", None)
    manifest["counts"].pop("discarded_reviewer_1_first_pass_records_included", None)
    manifest["parameters"].pop("discarded_reviewer_1_first_pass_included", None)
    manifest["parameters"].pop(
        "reviewer_1_ai_comparison_exposure_before_recheck", None
    )
    write_json(output_dir / "manifest.json", manifest)
    return manifest


def main() -> None:
    manifest = build()
    print(
        json.dumps(
            {
                "analysis_id": manifest["analysis_id"],
                "sample_records": manifest["counts"]["sample_records"],
                "output_directory": OUTPUT_DIR.relative_to(ROOT).as_posix(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
