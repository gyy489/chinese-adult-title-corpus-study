"""Corrected aggregate for the two-coder 400-record AI-accuracy audit.

Version 2 preserves the completed formal coder databases and all numerical
agreement results.  It corrects one provenance error in version 1: the deleted
first run was a researcher interface pilot, whereas the current ``reviewer_1``
database belongs to a different, first-exposure formal coder.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from src.analysis.summarize_gendered_visibility_human_audit_400_v1 import (
    ROOT,
    sha256_file,
)
from src.analysis.summarize_gendered_visibility_human_audit_400_v1 import (
    build as build_v1,
)

OUTPUT_DIR = ROOT / "data/processed/gendered_visibility_human_audit_400_summary_v2"
CORRECTION = (
    ROOT / "config/gendered_visibility_human_audit_identity_correction_v1.json"
)
SCRIPT = Path(__file__).resolve()
ANALYSIS_DATE = "2026-08-20"
ANALYSIS_ID = "gendered_visibility_human_audit_400_summary_v2"
NUMERIC_OUTPUTS = ("field_agreement.csv", "pair_summary.csv")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_correction() -> dict[str, Any]:
    correction = json.loads(CORRECTION.read_text(encoding="utf-8"))
    formal = correction["formal_coders"]
    if not all(
        formal[coder]["first_exposure_to_fixed_400_record_sample"]
        and not formal[coder]["ai_labels_visible_during_coding"]
        for coder in ("reviewer_1", "reviewer_2")
    ):
        raise RuntimeError("formal-coder first-exposure/AI-blind correction is incomplete")
    if correction["excluded_researcher_pilot"]["records_in_current_audit_outputs"]:
        raise RuntimeError("researcher pilot must contribute zero formal-audit records")
    return correction


def build(output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    correction = load_correction()
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="human-audit-v2-") as temp_name:
        temp_dir = Path(temp_name)
        upstream = build_v1(temp_dir)
        upstream_provenance = json.loads(
            (temp_dir / "provenance.json").read_text(encoding="utf-8")
        )
        for name in NUMERIC_OUTPUTS:
            (output_dir / name).write_bytes((temp_dir / name).read_bytes())

    profiles = upstream_provenance["reviewer_profiles"]
    corrected_profiles = {
        "reviewer_1": {
            "completed_records": 400,
            "current_result": "independent_first_exposure_formal_review",
            "first_exposure_to_sample": True,
            "ai_blind": True,
            "distinct_from_researcher_pilot": True,
            "instruction_payload": (
                "v2 expanded explanatory layer over the shared semantic instrument"
            ),
            "database_status_counts": profiles["reviewer_1"][
                "database_status_counts"
            ],
            "database_logical_snapshot_sha256": profiles["reviewer_1"][
                "database_logical_snapshot_sha256"
            ],
            "codebook_sha256": profiles["reviewer_1"]["codebook_sha256"],
            "base_codebook_sha256": profiles["reviewer_1"][
                "base_codebook_sha256"
            ],
            "earliest_update": profiles["reviewer_1"]["earliest_update"],
            "latest_update": profiles["reviewer_1"]["latest_update"],
            "needs_adjudication_counts": profiles["reviewer_1"][
                "needs_adjudication_counts"
            ],
        },
        "reviewer_2": {
            "completed_records": 400,
            "current_result": "independent_first_exposure_formal_review",
            "first_exposure_to_sample": True,
            "ai_blind": True,
            "distinct_from_researcher_pilot": True,
            "instruction_payload": "v1 base explanatory layer",
            "database_status_counts": profiles["reviewer_2"][
                "database_status_counts"
            ],
            "database_logical_snapshot_sha256": profiles["reviewer_2"][
                "database_logical_snapshot_sha256"
            ],
            "codebook_sha256": profiles["reviewer_2"]["codebook_sha256"],
            "earliest_update": profiles["reviewer_2"]["earliest_update"],
            "latest_update": profiles["reviewer_2"]["latest_update"],
            "needs_adjudication_counts": profiles["reviewer_2"][
                "needs_adjudication_counts"
            ],
        },
    }
    provenance = {
        "analysis_date": ANALYSIS_DATE,
        "analysis_id": ANALYSIS_ID,
        "supersedes": "gendered_visibility_human_audit_400_summary_v1",
        "correction": (
            "The deleted run was a researcher interface pilot, not an earlier pass "
            "by formal coder 1. Both formal coders were first-exposure and AI-blind."
        ),
        "audit_role": "ai_coding_accuracy_validation_against_human_references",
        "adjudication": "none",
        "automated_labels_modified": False,
        "completion_evidence": "both formal-coder databases contain 400 reviewed rows",
        "corpus": upstream_provenance["corpus"],
        "corpus_records": upstream_provenance["corpus_records"],
        "fields": upstream_provenance["fields"],
        "privacy": upstream_provenance["privacy"],
        "reviewer_profiles": corrected_profiles,
        "excluded_researcher_pilot": correction["excluded_researcher_pilot"],
        "shared_formal_audit_conditions": correction[
            "shared_formal_audit_conditions"
        ],
        "sample": upstream_provenance["sample"],
        "source_design_manifest_status": upstream_provenance[
            "source_design_manifest_status"
        ],
        "interpretation_limits": [
            "Accuracy is evaluated relative to two independent human reference judgments.",
            "The formal coders used the same fields, option codes, review application, and server-side semantic contract.",
            "No adjudicated consensus labels were created and no frozen AI label was overwritten.",
            "Rare-category and small conditional-denominator results remain directional or inconclusive.",
            "Exact agreement and Cohen's kappa must be read together because marginal imbalance can depress kappa.",
        ],
    }
    provenance_path = output_dir / "provenance.json"
    write_json(provenance_path, provenance)

    readme = """# Gendered visibility human-audit aggregate (v2)

This corrected aggregate validates frozen AI coding against two independent
formal human coders on the same fixed, stratified 400-record sample. Both formal
coders encountered the sampled titles for the first time and did not see AI
labels. The earlier deleted run was performed by the researcher to identify
interface and guidance problems; it was not a pass by formal coder 1 and
contributes zero records here.

`field_agreement.csv` and `pair_summary.csv` report AI--human and human--human
agreement for the 16 semantic fields. The formal coders used the same review
application, fields, option codes, and server-side semantic contract. One
database records an expanded explanatory layer, which did not change the fields
or codes. The audit evaluates AI coding accuracy relative to independent human
reference judgments and does not replace the frozen full-corpus labels.

Rebuild with:

```bash
scripts/evosci-python -m src.analysis.summarize_gendered_visibility_human_audit_400_v2
```
"""
    readme_path = output_dir / "README.md"
    readme_path.write_text(readme, encoding="utf-8")

    input_hashes = dict(upstream["inputs"])
    input_hashes[CORRECTION.relative_to(ROOT).as_posix()] = sha256_file(CORRECTION)
    input_hashes[SCRIPT.relative_to(ROOT).as_posix()] = sha256_file(SCRIPT)
    output_paths = [
        *(output_dir / name for name in NUMERIC_OUTPUTS),
        provenance_path,
        readme_path,
    ]
    manifest = {
        "analysis_date": ANALYSIS_DATE,
        "analysis_id": ANALYSIS_ID,
        "status": "complete_frozen_aggregate_correction",
        "supersedes": "gendered_visibility_human_audit_400_summary_v1",
        "parameters": {
            "sample_records": 400,
            "formal_coders": 2,
            "formal_coders_first_exposure": True,
            "formal_coders_ai_blind": True,
            "researcher_pilot_included": False,
            "accuracy_reference": "independent_human_coding",
            "adjudication": "none",
        },
        "counts": {
            "corpus_records": upstream["counts"]["corpus_records"],
            "sample_records": 400,
            "reviewer_1_reviewed_records": 400,
            "reviewer_2_reviewed_records": 400,
            "researcher_pilot_records_included": 0,
            "semantic_fields": upstream["counts"]["semantic_fields"],
            "pairwise_field_rows": upstream["counts"]["pairwise_field_rows"],
        },
        "inputs": input_hashes,
        "private_database_logical_snapshots": upstream[
            "private_database_logical_snapshots"
        ],
        "outputs": {path.name: sha256_file(path) for path in output_paths},
    }
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
