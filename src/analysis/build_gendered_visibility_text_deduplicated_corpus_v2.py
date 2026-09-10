"""Build the audit-corrected unique-text manuscript corpus (v2).

The immutable v1 corpus remains the provenance source.  This correction changes
the analytical unit from a record-key-unique title record to one unique final
``deidentified_title`` string.  Within each repeated ``title_sha256`` group, the
representative is selected by the lexicographically smallest
``record_key_sha256``.  The rule is deterministic and does not inspect model
outputs, sampling track, or substantive labels. Source layer and item index
serve only as tie-breakers after an identical record-key hash.

No title text is written to the processed corpus.  The private membership keeps
only hashes and source pointers required to reconstruct the frozen annotation.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = Path(__file__).resolve()
V1_MANIFEST = ROOT / "data/processed/gendered_visibility_paper_corpus_v1/manifest.json"
V1_MEMBERSHIP = ROOT / (
    "data/processed/gendered_visibility_paper_corpus_v1/private/"
    "analytical_corpus_membership.csv"
)
DEIDENTIFIED_FRAME = ROOT / (
    "data/interim/11_deidentification/titles_for_analysis_deidentified.csv"
)
CANDIDATE_FRAME = ROOT / (
    "data/interim/56_gendered_asymmetric_visibility_full_annotation_90727_v2/"
    "private/full_manifest_90727.csv"
)
FROZEN_RECORDS = ROOT / (
    "data/interim/51_gendered_argument_visibility_scaleup_5000_v1/"
    "private/validation_5000_final_records.jsonl"
)
FROZEN_SAMPLE = ROOT / (
    "data/interim/51_gendered_argument_visibility_scaleup_5000_v1/"
    "private/sample_manifest.csv"
)
RUN70_RECORDS = ROOT / (
    "data/interim/70_gendered_argument_visibility_budget_adaptive_20000_v1/"
    "private/final_records.jsonl"
)
DEFAULT_OUTPUT_DIR = (
    ROOT / "data/processed/gendered_visibility_paper_corpus_v2_text_deduplicated"
)

EXPECTED_DEIDENTIFIED_RECORDS = 91_172
EXPECTED_DEIDENTIFIED_UNIQUE_TEXTS = 89_538
EXPECTED_CANDIDATE_RECORDS = 90_727
EXPECTED_CANDIDATE_UNIQUE_TEXTS = 89_161
EXPECTED_V1_CORPUS_RECORDS = 38_623
EXPECTED_V2_CORPUS_TEXTS = 38_300


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def title_hash(title: str) -> str:
    return hashlib.sha256(title.encode("utf-8")).hexdigest()


def record_key_hash(record_id: str) -> str:
    return hashlib.sha256(record_id.encode("utf-8")).hexdigest()


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def validate_frames() -> dict[str, int]:
    deidentified_rows = load_csv(DEIDENTIFIED_FRAME)
    if len(deidentified_rows) != EXPECTED_DEIDENTIFIED_RECORDS:
        raise RuntimeError("deidentified record-frame count drifted")
    deidentified_hashes = [title_hash(row["deidentified_title"]) for row in deidentified_rows]
    deidentified_counts = Counter(deidentified_hashes)
    if len(deidentified_counts) != EXPECTED_DEIDENTIFIED_UNIQUE_TEXTS:
        raise RuntimeError("deidentified unique-text count drifted")

    candidate_rows = load_csv(CANDIDATE_FRAME)
    if len(candidate_rows) != EXPECTED_CANDIDATE_RECORDS:
        raise RuntimeError("candidate record-frame count drifted")
    candidate_hashes = [row["title_sha256"] for row in candidate_rows]
    for row in candidate_rows:
        if row["title_sha256"] != title_hash(row["deidentified_title"]):
            raise RuntimeError("candidate title hash does not match final text")
    candidate_counts = Counter(candidate_hashes)
    if len(candidate_counts) != EXPECTED_CANDIDATE_UNIQUE_TEXTS:
        raise RuntimeError("candidate unique-text count drifted")
    if not set(candidate_counts).issubset(deidentified_counts):
        raise RuntimeError("candidate texts are not a subset of the deidentified frame")

    return {
        "deidentified_record_rows": len(deidentified_rows),
        "deidentified_unique_texts": len(deidentified_counts),
        "deidentified_repeated_text_groups": sum(
            count > 1 for count in deidentified_counts.values()
        ),
        "deidentified_extra_rows_beyond_first": sum(
            count - 1 for count in deidentified_counts.values() if count > 1
        ),
        "candidate_record_rows": len(candidate_rows),
        "candidate_unique_texts": len(candidate_counts),
        "candidate_repeated_text_groups": sum(
            count > 1 for count in candidate_counts.values()
        ),
        "candidate_extra_rows_beyond_first": sum(
            count - 1 for count in candidate_counts.values() if count > 1
        ),
        "short_record_rows_excluded": len(deidentified_rows) - len(candidate_rows),
        "short_unique_texts_excluded": len(deidentified_counts) - len(candidate_counts),
    }


def build_frame_memberships() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build private hash-only memberships for both full final-text layers."""

    def deduplicate(
        rows: list[dict[str, str]],
        *,
        stored_hash: bool,
        output_index_name: str,
    ) -> list[dict[str, Any]]:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for source_row_index, row in enumerate(rows, start=1):
            observed_hash = title_hash(row["deidentified_title"])
            if stored_hash and row["title_sha256"] != observed_hash:
                raise RuntimeError("stored candidate hash does not match final text")
            groups[observed_hash].append(
                {
                    "source_row_index": source_row_index,
                    "source_item_index": int(row.get("item_index") or source_row_index),
                    "record_key_sha256": record_key_hash(row["record_id"]),
                }
            )

        selected: list[dict[str, Any]] = []
        for text_digest, group in groups.items():
            representative = min(
                group,
                key=lambda item: (
                    item["record_key_sha256"],
                    item["source_item_index"],
                ),
            )
            selected.append(
                {
                    "title_sha256": text_digest,
                    "representative_record_key_sha256": representative[
                        "record_key_sha256"
                    ],
                    "representative_source_row_index": representative[
                        "source_row_index"
                    ],
                    "representative_source_item_index": representative[
                        "source_item_index"
                    ],
                    "record_rows_with_final_text": len(group),
                }
            )
        selected.sort(key=lambda item: item["title_sha256"])
        for index, item in enumerate(selected, start=1):
            item[output_index_name] = index
        return selected

    deidentified = deduplicate(
        load_csv(DEIDENTIFIED_FRAME),
        stored_hash=False,
        output_index_name="deidentified_text_index",
    )
    candidate = deduplicate(
        load_csv(CANDIDATE_FRAME),
        stored_hash=True,
        output_index_name="candidate_text_index",
    )
    if len(deidentified) != EXPECTED_DEIDENTIFIED_UNIQUE_TEXTS:
        raise RuntimeError("deidentified text membership count drifted")
    if len(candidate) != EXPECTED_CANDIDATE_UNIQUE_TEXTS:
        raise RuntimeError("candidate text membership count drifted")
    if not {row["title_sha256"] for row in candidate}.issubset(
        {row["title_sha256"] for row in deidentified}
    ):
        raise RuntimeError("candidate text membership is not a deidentified subset")
    return deidentified, candidate


def build_membership() -> tuple[list[dict[str, Any]], dict[str, int]]:
    v1_manifest = json.loads(V1_MANIFEST.read_text(encoding="utf-8"))
    expected_membership_hash = v1_manifest["outputs"].get(
        V1_MEMBERSHIP.relative_to(ROOT).as_posix()
    )
    if expected_membership_hash != sha256_file(V1_MEMBERSHIP):
        raise RuntimeError("v1 membership checksum mismatch")

    rows = load_csv(V1_MEMBERSHIP)
    if len(rows) != EXPECTED_V1_CORPUS_RECORDS:
        raise RuntimeError("v1 paper-corpus count drifted")

    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[row["title_sha256"]].append(row)
    if len(groups) != EXPECTED_V2_CORPUS_TEXTS:
        raise RuntimeError("v2 unique-text corpus count drifted")

    selected: list[dict[str, Any]] = []
    for group in groups.values():
        representative = min(
            group,
            key=lambda row: (
                row["record_key_sha256"],
                row["source_layer"],
                int(row["source_item_index"]),
            ),
        )
        selected.append(
            {
                **representative,
                "duplicate_text_group_size_in_v1": len(group),
            }
        )
    selected.sort(key=lambda row: (row["title_sha256"], row["record_key_sha256"]))
    for index, row in enumerate(selected, start=1):
        row["corpus_index"] = index

    if len({row["title_sha256"] for row in selected}) != len(selected):
        raise RuntimeError("v2 membership still contains duplicate final texts")
    if len({row["record_key_sha256"] for row in selected}) != len(selected):
        raise RuntimeError("v2 membership contains duplicate record keys")

    source_counts = Counter(row["source_layer"] for row in selected)
    repeated_groups = sum(len(group) > 1 for group in groups.values())
    extra_records = sum(len(group) - 1 for group in groups.values() if len(group) > 1)
    return selected, {
        "v1_valid_record_members": len(rows),
        "v1_distinct_final_texts": len(groups),
        "v1_repeated_final_text_groups": repeated_groups,
        "v1_extra_records_beyond_first": extra_records,
        "v2_unique_text_members": len(selected),
        "v2_frozen_layer_representatives": source_counts["frozen_v0_3_5000"],
        "v2_later_sequence_representatives": source_counts["run70_complete"],
    }


def write_outputs(
    output_dir: Path,
    membership: list[dict[str, Any]],
    deidentified_text_membership: list[dict[str, Any]],
    candidate_text_membership: list[dict[str, Any]],
    corpus_counts: dict[str, int],
    frame_counts: dict[str, int],
) -> dict[str, Any]:
    private_dir = output_dir / "private"
    private_dir.mkdir(parents=True, exist_ok=True)
    membership_path = private_dir / "analytical_corpus_membership.csv"
    fieldnames = [
        "corpus_index",
        "source_layer",
        "source_item_index",
        "record_key_sha256",
        "title_sha256",
        "record_validity",
        "duplicate_text_group_size_in_v1",
    ]
    with membership_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(membership)
    os.chmod(membership_path, 0o600)

    frame_membership_specs = (
        (
            private_dir / "unique_deidentified_text_membership.csv",
            deidentified_text_membership,
            "deidentified_text_index",
        ),
        (
            private_dir / "unique_candidate_text_membership.csv",
            candidate_text_membership,
            "candidate_text_index",
        ),
    )
    frame_membership_paths: list[Path] = []
    for path, rows, index_field in frame_membership_specs:
        fieldnames = [
            index_field,
            "title_sha256",
            "representative_record_key_sha256",
            "representative_source_row_index",
            "representative_source_item_index",
            "record_rows_with_final_text",
        ]
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        os.chmod(path, 0o600)
        frame_membership_paths.append(path)

    all_counts = {**frame_counts, **corpus_counts}
    manifest = {
        "created_at": utc_now(),
        "freeze_id": "gendered_visibility_paper_corpus_v2_text_deduplicated",
        "decision_date": "2026-08-22",
        "status": "audit_corrected_unique_final_text_corpus",
        "supersedes_for_manuscript_analysis": "gendered_visibility_paper_corpus_v1",
        "selection_rule": {
            "include": (
                "one representative per title_sha256 among contract-valid v0.3 "
                "outputs with output.record_validity=valid"
            ),
            "representative_rule": (
                "lexicographically smallest record_key_sha256; ties by source_layer "
                "and source_item_index"
            ),
            "rule_independence": (
                "representative selection does not inspect model outputs, substantive "
                "labels, or sampling track; source layer and source item index are "
                "used only as tie-breakers after an identical record-key hash"
            ),
        },
        "scope": {
            "paper_analytical_corpus_unique_final_texts": len(membership),
            "source_frame_record_rows_before_text_deduplication": frame_counts[
                "candidate_record_rows"
            ],
            "source_frame_unique_final_texts": frame_counts[
                "candidate_unique_texts"
            ],
            "unique_final_texts_outside_paper_corpus": (
                frame_counts["candidate_unique_texts"] - len(membership)
            ),
            "unit": "unique deidentified title text identified by title_sha256",
            "interpretation": (
                "The included units are all distinct final title texts represented "
                "among valid completed annotations. Historical record-level source "
                "outputs remain immutable provenance."
            ),
            "inference_boundary": (
                "Do not generalize corpus-descriptive proportions to the earlier "
                "89,161-unique-text candidate frame or to all Chinese adult-film titles."
            ),
        },
        "counts": all_counts,
        "inputs": {
            path.relative_to(ROOT).as_posix(): sha256_file(path)
            for path in (
                V1_MANIFEST,
                V1_MEMBERSHIP,
                DEIDENTIFIED_FRAME,
                CANDIDATE_FRAME,
                FROZEN_RECORDS,
                FROZEN_SAMPLE,
                RUN70_RECORDS,
                SCRIPT,
            )
        },
        "outputs": {
            path.relative_to(ROOT).as_posix(): sha256_file(path)
            for path in (membership_path, *frame_membership_paths)
        },
        "annotation_status": "closed_no_further_paid_annotation",
        "api_calls": 0,
        "raw_data_files_read": 0,
        "frozen_source_outputs_rewritten": 0,
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    frame_counts = validate_frames()
    deidentified_text_membership, candidate_text_membership = (
        build_frame_memberships()
    )
    membership, corpus_counts = build_membership()
    manifest = write_outputs(
        args.output_dir,
        membership,
        deidentified_text_membership,
        candidate_text_membership,
        corpus_counts,
        frame_counts,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
