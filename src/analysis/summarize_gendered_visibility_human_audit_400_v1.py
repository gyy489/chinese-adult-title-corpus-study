"""Aggregate the completed 400-record two-reviewer construct audit.

The source databases and sealed AI reference are private, row-level inputs.  This
module reads them without modifying them and publishes aggregate agreement only.
No title, blind identifier, record identifier, or record key is written to the
processed output.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import sqlite3
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
AUDIT_DIR = ROOT / "data/interim/72_gendered_visibility_human_review_400_v1"
OUTPUT_DIR = (
    ROOT / "data/processed/gendered_visibility_human_audit_400_summary_v1"
)
REVIEW_MANIFEST = AUDIT_DIR / "manifest.json"
SAMPLING_FRAME = AUDIT_DIR / "sampling_frame.csv"
SEALED_REFERENCE = AUDIT_DIR / "private/sealed_reference.jsonl"
REVIEWER_DATABASES = {
    "reviewer_1": AUDIT_DIR / "coders/review_reviewer_1.sqlite3",
    "reviewer_2": AUDIT_DIR / "coders/review_reviewer_2.sqlite3",
}
BASE_CODEBOOK = ROOT / "config/gendered_visibility_human_review_400_v1.json"
RECHECK_CODEBOOK = (
    ROOT / "config/gendered_visibility_human_review_400_recheck_v2.json"
)
ANALYSIS_DATE = "2026-08-19"
EXPECTED_RECORDS = 400
EXPECTED_CORPUS_RECORDS = 38_623
PAIR_DEFINITIONS = (
    ("ai_vs_reviewer_1", "frozen_ai_v0_3", "reviewer_1"),
    ("ai_vs_reviewer_2", "frozen_ai_v0_3", "reviewer_2"),
    ("reviewer_1_vs_reviewer_2", "reviewer_1", "reviewer_2"),
)


@dataclass(frozen=True)
class ReviewerSnapshot:
    reviewer_id: str
    annotations: dict[str, dict[str, str]]
    metadata: dict[str, str]
    status_counts: dict[str, int]
    needs_adjudication_counts: dict[str, int]
    logical_sha256: str
    earliest_update: str
    latest_update: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"refusing to write empty table: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_codebook_contract() -> tuple[list[str], dict[str, set[str]]]:
    codebook = json.loads(BASE_CODEBOOK.read_text(encoding="utf-8"))
    semantic_fields = [
        field["id"]
        for field in codebook["fields"]
        if field["id"] not in {"needs_adjudication", "evidence_notes"}
    ]
    option_sets = {
        name: {option["id"] for option in options}
        for name, options in codebook["option_sets"].items()
    }
    domains = {
        field["id"]: option_sets[field["option_set"]]
        for field in codebook["fields"]
        if field["id"] in semantic_fields
    }
    if len(semantic_fields) != 16:
        raise RuntimeError(f"expected 16 semantic fields, found {len(semantic_fields)}")
    return semantic_fields, domains


def load_sampling_frame() -> tuple[dict[str, float], dict[str, int]]:
    manifest = json.loads(REVIEW_MANIFEST.read_text(encoding="utf-8"))
    rows = read_csv(SAMPLING_FRAME)
    if len(rows) != EXPECTED_RECORDS:
        raise RuntimeError(f"sampling frame has {len(rows)} rows, expected 400")
    if len({row["blind_id"] for row in rows}) != EXPECTED_RECORDS:
        raise RuntimeError("sampling-frame blind identifiers are not unique")
    weights = {row["blind_id"]: float(row["design_weight"]) for row in rows}
    strata = Counter(row["audit_stratum"] for row in rows)
    if dict(strata) != manifest["quotas"]:
        raise RuntimeError(f"sampling quotas drifted: {dict(strata)}")
    if not math.isclose(sum(weights.values()), EXPECTED_CORPUS_RECORDS, abs_tol=1e-6):
        raise RuntimeError("design weights do not recover the 38,623-record corpus")
    return weights, dict(sorted(strata.items()))


def load_ai_reference(
    fields: list[str], domains: Mapping[str, set[str]]
) -> dict[str, dict[str, str]]:
    rows = [
        json.loads(line)
        for line in SEALED_REFERENCE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(rows) != EXPECTED_RECORDS:
        raise RuntimeError(f"sealed reference has {len(rows)} rows, expected 400")
    outputs: dict[str, dict[str, str]] = {}
    for row in rows:
        blind_id = row["blind_id"]
        output = row["reference_output"]
        if set(output) != set(fields):
            raise RuntimeError("sealed reference field contract drifted")
        for field in fields:
            if output[field] not in domains[field]:
                raise RuntimeError(f"invalid AI category for {field}")
        if blind_id in outputs:
            raise RuntimeError("duplicate blind identifier in sealed reference")
        outputs[blind_id] = output
    return outputs


def load_reviewer_snapshot(
    reviewer_id: str,
    path: Path,
    fields: list[str],
    domains: Mapping[str, set[str]],
) -> ReviewerSnapshot:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        metadata = {
            row["key"]: row["value"]
            for row in connection.execute("SELECT key,value FROM metadata")
        }
        raw_rows = list(
            connection.execute(
                """
                SELECT r.blind_id, a.annotation_json, a.status, a.revision,
                       a.updated_at
                FROM annotations AS a
                JOIN records AS r ON r.record_id = a.record_id
                ORDER BY r.blind_id
                """
            )
        )
        record_count = connection.execute(
            "SELECT COUNT(*) FROM records"
        ).fetchone()[0]
        connection.commit()
    finally:
        connection.close()

    if metadata.get("coder_id") != reviewer_id:
        raise RuntimeError(f"reviewer identity mismatch for {reviewer_id}")
    if metadata.get("expected_records") != str(EXPECTED_RECORDS):
        raise RuntimeError(f"expected-record metadata mismatch for {reviewer_id}")
    if record_count != EXPECTED_RECORDS or len(raw_rows) != EXPECTED_RECORDS:
        raise RuntimeError(f"{reviewer_id} is not a complete 400-record database")

    annotations: dict[str, dict[str, str]] = {}
    status_counts: Counter[str] = Counter()
    needs_adjudication_counts: Counter[str] = Counter()
    logical_rows: list[dict[str, Any]] = []
    updates: list[str] = []
    for row in raw_rows:
        annotation = json.loads(row["annotation_json"])
        if set(fields) - set(annotation):
            raise RuntimeError(f"{reviewer_id} has an incomplete semantic annotation")
        for field in fields:
            if annotation[field] not in domains[field]:
                raise RuntimeError(f"invalid {reviewer_id} category for {field}")
        blind_id = row["blind_id"]
        if blind_id in annotations:
            raise RuntimeError(f"duplicate blind identifier for {reviewer_id}")
        annotations[blind_id] = {field: annotation[field] for field in fields}
        status_counts[row["status"]] += 1
        needs_adjudication_counts[annotation["needs_adjudication"]] += 1
        updates.append(row["updated_at"])
        logical_rows.append(
            {
                "annotation": annotations[blind_id],
                "blind_id": blind_id,
                "revision": row["revision"],
                "status": row["status"],
            }
        )
    if status_counts != {"reviewed": EXPECTED_RECORDS}:
        raise RuntimeError(f"{reviewer_id} is not fully reviewed: {dict(status_counts)}")

    return ReviewerSnapshot(
        reviewer_id=reviewer_id,
        annotations=annotations,
        metadata=metadata,
        status_counts=dict(status_counts),
        needs_adjudication_counts=dict(needs_adjudication_counts),
        logical_sha256=canonical_sha256(logical_rows),
        earliest_update=min(updates),
        latest_update=max(updates),
    )


def cohen_kappa(
    first: Iterable[str], second: Iterable[str]
) -> tuple[float | None, str]:
    first_values = list(first)
    second_values = list(second)
    if len(first_values) != len(second_values) or not first_values:
        raise ValueError("kappa inputs must have equal, nonzero length")
    count = len(first_values)
    observed = sum(
        left == right for left, right in zip(first_values, second_values)
    ) / count
    first_counts = Counter(first_values)
    second_counts = Counter(second_values)
    expected = sum(
        (first_counts[label] / count) * (second_counts[label] / count)
        for label in first_counts.keys() | second_counts.keys()
    )
    if math.isclose(expected, 1.0, abs_tol=1e-15):
        return None, "undefined_no_marginal_variation"
    return (observed - expected) / (1.0 - expected), "defined"


def format_number(value: float | None, digits: int = 6) -> str:
    return "" if value is None else f"{value:.{digits}f}"


def build(output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    fields, domains = load_codebook_contract()
    weights, strata = load_sampling_frame()
    ai = load_ai_reference(fields, domains)
    reviewer_snapshots = {
        reviewer_id: load_reviewer_snapshot(reviewer_id, path, fields, domains)
        for reviewer_id, path in REVIEWER_DATABASES.items()
    }
    design_manifest = json.loads(REVIEW_MANIFEST.read_text(encoding="utf-8"))
    base_hash = sha256_file(BASE_CODEBOOK)
    recheck_hash = sha256_file(RECHECK_CODEBOOK)
    queue_hash = design_manifest["outputs"][
        "data/interim/72_gendered_visibility_human_review_400_v1/private/blind_queue.csv"
    ]
    for reviewer_id, snapshot in reviewer_snapshots.items():
        if snapshot.metadata.get("queue_sha256") != queue_hash:
            raise RuntimeError(f"{reviewer_id} queue contract drifted")
        if snapshot.metadata.get("review_contract") != (
            "gendered_visibility_paper_core_v1"
        ):
            raise RuntimeError(f"{reviewer_id} semantic review contract drifted")
    if reviewer_snapshots["reviewer_1"].metadata["codebook_sha256"] != recheck_hash:
        raise RuntimeError("reviewer 1 did not use the frozen v2 guidance")
    if reviewer_snapshots["reviewer_1"].metadata["base_codebook_sha256"] != base_hash:
        raise RuntimeError("reviewer 1 base semantic contract drifted")
    if reviewer_snapshots["reviewer_2"].metadata["codebook_sha256"] != base_hash:
        raise RuntimeError("reviewer 2 did not use the frozen v1 guidance")

    expected_ids = set(weights)
    if set(ai) != expected_ids:
        raise RuntimeError("AI and sampling-frame record sets differ")
    for snapshot in reviewer_snapshots.values():
        if set(snapshot.annotations) != expected_ids:
            raise RuntimeError(f"{snapshot.reviewer_id} record set differs")

    sources = {
        "frozen_ai_v0_3": ai,
        **{
            reviewer_id: snapshot.annotations
            for reviewer_id, snapshot in reviewer_snapshots.items()
        },
    }
    ids = sorted(expected_ids)
    total_weight = sum(weights.values())
    field_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []

    for pair, first_name, second_name in PAIR_DEFINITIONS:
        first = sources[first_name]
        second = sources[second_name]
        pair_exact = 0
        pair_weighted_exact = 0.0
        for field in fields:
            first_values = [first[blind_id][field] for blind_id in ids]
            second_values = [second[blind_id][field] for blind_id in ids]
            exact_n = sum(
                left == right
                for left, right in zip(first_values, second_values)
            )
            weighted_exact = sum(
                weights[blind_id]
                for blind_id in ids
                if first[blind_id][field] == second[blind_id][field]
            )
            kappa, kappa_status = cohen_kappa(first_values, second_values)
            unweighted_percent = exact_n / EXPECTED_RECORDS * 100
            weighted_percent = weighted_exact / total_weight * 100
            field_rows.append(
                {
                    "pair": pair,
                    "first_source": first_name,
                    "second_source": second_name,
                    "field": field,
                    "records": EXPECTED_RECORDS,
                    "exact_agreement_n": exact_n,
                    "exact_agreement_percent": f"{unweighted_percent:.6f}",
                    "cohen_kappa": format_number(kappa),
                    "kappa_status": kappa_status,
                    "design_weighted_exact_agreement_percent": (
                        f"{weighted_percent:.6f}"
                    ),
                    "weighted_minus_unweighted_pp": (
                        f"{weighted_percent - unweighted_percent:.6f}"
                    ),
                }
            )
            pair_exact += exact_n
            pair_weighted_exact += weighted_exact

        total_decisions = EXPECTED_RECORDS * len(fields)
        summary_rows.append(
            {
                "pair": pair,
                "fields": len(fields),
                "records": EXPECTED_RECORDS,
                "decisions": total_decisions,
                "exact_agreement_n": pair_exact,
                "micro_exact_agreement_percent": (
                    f"{pair_exact / total_decisions * 100:.6f}"
                ),
                "design_weighted_micro_exact_agreement_percent": (
                    f"{pair_weighted_exact / (total_weight * len(fields)) * 100:.6f}"
                ),
                "micro_kappa": "",
                "micro_kappa_status": "not_computed_across_distinct_field_domains",
            }
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    field_path = output_dir / "field_agreement.csv"
    summary_path = output_dir / "pair_summary.csv"
    provenance_path = output_dir / "provenance.json"
    readme_path = output_dir / "README.md"
    manifest_path = output_dir / "manifest.json"
    write_csv(field_path, field_rows)
    write_csv(summary_path, summary_rows)

    reviewer_profiles = {
        "reviewer_1": {
            "completed_records": EXPECTED_RECORDS,
            "current_result": "same_400_record_recheck_under_v2",
            "prior_title_exposure": "same_400_records_seen_in_discarded_first_pass",
            "discarded_first_pass_result_status": (
                "deleted_before_analysis_and_excluded_from_all_outputs"
            ),
            "discarded_first_pass_records_in_analysis": 0,
            "ai_comparison_exposure_before_recheck": "unverified_by_researcher",
            "first_exposure_independence_claim_permitted": False,
            "database_status_counts": reviewer_snapshots[
                "reviewer_1"
            ].status_counts,
            "database_logical_snapshot_sha256": reviewer_snapshots[
                "reviewer_1"
            ].logical_sha256,
            "instruction_version": "v2.0.0_direct_guidance_recheck",
            "codebook_sha256": reviewer_snapshots["reviewer_1"].metadata[
                "codebook_sha256"
            ],
            "base_codebook_sha256": reviewer_snapshots["reviewer_1"].metadata[
                "base_codebook_sha256"
            ],
            "earliest_update": reviewer_snapshots["reviewer_1"].earliest_update,
            "latest_update": reviewer_snapshots["reviewer_1"].latest_update,
            "needs_adjudication_counts": reviewer_snapshots[
                "reviewer_1"
            ].needs_adjudication_counts,
        },
        "reviewer_2": {
            "completed_records": EXPECTED_RECORDS,
            "database_status_counts": reviewer_snapshots[
                "reviewer_2"
            ].status_counts,
            "database_logical_snapshot_sha256": reviewer_snapshots[
                "reviewer_2"
            ].logical_sha256,
            "instruction_version": "v1.0.0_original_guidance",
            "codebook_sha256": reviewer_snapshots["reviewer_2"].metadata[
                "codebook_sha256"
            ],
            "earliest_update": reviewer_snapshots["reviewer_2"].earliest_update,
            "latest_update": reviewer_snapshots["reviewer_2"].latest_update,
            "needs_adjudication_counts": reviewer_snapshots[
                "reviewer_2"
            ].needs_adjudication_counts,
        },
    }

    provenance = {
        "analysis_date": ANALYSIS_DATE,
        "analysis_id": "gendered_visibility_human_audit_400_summary_v1",
        "audit_role": "quality_audit_and_possible_calibration_not_gold_standard",
        "adjudication": "none",
        "automated_labels_modified": False,
        "completion_evidence": "both reviewer databases contain 400 reviewed rows",
        "corpus": "gendered_visibility_paper_corpus_v1",
        "corpus_records": EXPECTED_CORPUS_RECORDS,
        "fields": fields,
        "privacy": {
            "aggregate_only": True,
            "contains_blind_id": False,
            "contains_record_key": False,
            "contains_title_text": False,
        },
        "reviewer_profiles": reviewer_profiles,
        "sample": {
            "records": EXPECTED_RECORDS,
            "sampling_method": (
                "fixed-seed stratified sampling across three audit strata"
            ),
            "strata": strata,
            "design_weight_total": round(total_weight, 6),
            "unweighted_metrics_scope": "the fixed 400-record audit sample",
            "weighted_agreement_estimand": (
                "Horvitz-Thompson weighted agreement estimate for the 38,623-record corpus"
            ),
        },
        "source_design_manifest_status": design_manifest["status"],
        "interpretation_limits": [
            "Human coding is not treated as a gold standard.",
            "Reviewer 1 repeated the same 400 titles under clarified v2 instructions after a discarded first pass; reviewer 2 used v1 instructions on a first-exposure pass.",
            "Reviewer 1 prior AI-comparison exposure is unverified, so the recheck is not presented as an AI-blind or first-exposure replicate.",
            "Human-human agreement is therefore a cross-review diagnostic under different exposure and instruction conditions, not a same-protocol reliability coefficient.",
            "No disagreements were adjudicated and no frozen AI labels were overwritten.",
            "Rare-category and small conditional-denominator results remain directional or inconclusive.",
            "Exact agreement and Cohen's kappa must be read together because marginal imbalance can depress kappa.",
        ],
    }
    write_json(provenance_path, provenance)

    readme = """# Gendered visibility human-audit aggregate (v1)

This directory contains privacy-safe aggregate comparisons for the completed
400-record human coding audit. It contains no title text, blind identifier, record
identifier, record key, reviewer note, or row-level label.

`field_agreement.csv` reports, for each of the 16 frozen semantic fields,
unweighted exact agreement, Cohen's kappa, and design-weighted exact agreement
for AI--reviewer 1, AI--reviewer 2, and reviewer 1--reviewer 2. The weighted
percentage applies the frozen audit-stratum weights; Cohen's kappa is calculated
on the fixed 400-record sample and is not design weighted.

`pair_summary.csv` reports aggregate exact agreement across 6,400 decisions per
pair. A single micro kappa is deliberately not calculated because the 16 fields
have distinct category domains.

Reviewer 1 repeated the same 400-title queue using clarified v2 instructions
after a discarded first pass. The discarded row-level result was permanently
deleted and contributes zero records to these outputs. Prior title exposure is
known and prior AI-comparison exposure is unverified, so this output is not
presented as a first-exposure or AI-blind replicate. Reviewer 2 completed a
first-exposure pass using the original v1 instructions. Both retained the same 16
semantic fields and option codes. There was no adjudication, neither human is
treated as a gold standard, and frozen AI labels were not overwritten.

Rebuild from the private local sources with:

```bash
scripts/evosci-python -m src.analysis.summarize_gendered_visibility_human_audit_400_v1
```
"""
    readme_path.write_text(readme, encoding="utf-8")

    input_files = (
        REVIEW_MANIFEST,
        SAMPLING_FRAME,
        SEALED_REFERENCE,
        BASE_CODEBOOK,
        RECHECK_CODEBOOK,
        Path(__file__).resolve(),
    )
    manifest = {
        "analysis_date": ANALYSIS_DATE,
        "analysis_id": "gendered_visibility_human_audit_400_summary_v1",
        "status": "complete_frozen_aggregate",
        "parameters": {
            "fields": fields,
            "pairs": [pair for pair, _, _ in PAIR_DEFINITIONS],
            "sample_records": EXPECTED_RECORDS,
            "use_design_weights_for_weighted_agreement": True,
            "cohen_kappa_design_weighted": False,
            "adjudication": "none",
            "discarded_reviewer_1_first_pass_included": False,
            "reviewer_1_ai_comparison_exposure_before_recheck": (
                "unverified_by_researcher"
            ),
        },
        "counts": {
            "corpus_records": EXPECTED_CORPUS_RECORDS,
            "sample_records": EXPECTED_RECORDS,
            "reviewer_1_reviewed_records": EXPECTED_RECORDS,
            "reviewer_2_reviewed_records": EXPECTED_RECORDS,
            "discarded_reviewer_1_first_pass_records_included": 0,
            "semantic_fields": len(fields),
            "pairwise_field_rows": len(field_rows),
        },
        "inputs": {
            path.relative_to(ROOT).as_posix(): sha256_file(path)
            for path in input_files
        },
        "private_database_logical_snapshots": {
            reviewer_id: snapshot.logical_sha256
            for reviewer_id, snapshot in reviewer_snapshots.items()
        },
        "outputs": {
            path.name: sha256_file(path)
            for path in (field_path, summary_path, provenance_path, readme_path)
        },
    }
    write_json(manifest_path, manifest)
    return manifest


def main() -> None:
    manifest = build()
    print(
        json.dumps(
            {
                "analysis_id": manifest["analysis_id"],
                "sample_records": manifest["counts"]["sample_records"],
                "pairwise_field_rows": manifest["counts"]["pairwise_field_rows"],
                "output_directory": OUTPUT_DIR.relative_to(ROOT).as_posix(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
