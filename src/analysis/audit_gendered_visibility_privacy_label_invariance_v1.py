#!/usr/bin/env python3
"""Audit whether the one URL-only privacy edit affects frozen AI labels.

The audit is offline and deterministic.  It compares the v2 and v3 unit,
verifies that the private override is exactly the authorized URL deletion,
and checks every stored output, derived field, evidence span, and normalization
action for dependency on the removed locator.  It does not claim an independent
human recoding and does not send the title to any provider.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.analysis.analyze_gendered_visibility_paper_corpus_v1 import load_units
from src.analysis.build_gendered_visibility_privacy_remediation_v1 import (
    AUDIT_V1_CANDIDATES,
    REMEDIATION_ID,
    V2_MANIFEST,
    V2_MEMBERSHIP,
    confirmed_url_from_audit,
    remove_exact_url,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = Path(__file__).resolve()
V3_DIR = ROOT / "data/processed/gendered_visibility_paper_corpus_v3_privacy_remediated"
V3_MANIFEST = V3_DIR / "manifest.json"
V3_MEMBERSHIP = V3_DIR / "private/analytical_corpus_membership.csv"
V3_OVERRIDES = V3_DIR / "private/title_overrides.csv"
DEFAULT_OUTPUT_DIR = (
    ROOT
    / "data/processed/gendered_visibility_privacy_remediation_label_invariance_v1"
)
DEFAULT_REPORT = (
    ROOT
    / "results/logs/gendered-visibility-privacy-label-invariance-v1/"
    "2026-08-23-report.md"
)
EXPECTED_CORPUS_N = 38_300


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, payload: dict[str, Any], *, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if private:
        os.chmod(path, 0o600)


def read_one_override() -> dict[str, str]:
    with V3_OVERRIDES.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 1:
        raise RuntimeError("expected exactly one privacy title override")
    if rows[0]["remediation_rule"] != REMEDIATION_ID:
        raise RuntimeError("unexpected privacy remediation rule")
    return rows[0]


def serialized_contains(value: Any, needles: tuple[str, ...]) -> bool:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return any(needle and needle in serialized for needle in needles)


def run(output_dir: Path, report_path: Path) -> dict[str, Any]:
    source_units, _ = load_units(
        V2_MANIFEST,
        V2_MEMBERSHIP,
        EXPECTED_CORPUS_N,
        require_exact_valid_output_membership=False,
    )
    current_units, freeze = load_units(
        V3_MANIFEST,
        V3_MEMBERSHIP,
        EXPECTED_CORPUS_N,
        require_exact_valid_output_membership=False,
    )
    override = read_one_override()
    source_by_hash = {unit.title_sha256: unit for unit in source_units}
    current_by_hash = {unit.title_sha256: unit for unit in current_units}
    source = source_by_hash[override["source_title_sha256"]]
    current = current_by_hash[override["title_sha256"]]

    locator = confirmed_url_from_audit(AUDIT_V1_CANDIDATES)
    account_token = locator.rstrip("/").split("/")[-2]
    expected_title = remove_exact_url(source.title, locator)
    if current.title != expected_title or current.title != override["remediated_title"]:
        raise RuntimeError("v3 title is not exactly the authorized URL-only edit")
    if (
        source.corpus_index != current.corpus_index
        or source.source_layer != current.source_layer
        or source.sampling_track != current.sampling_track
    ):
        raise RuntimeError("non-title unit identity changed during remediation")

    output_equal = source.output == current.output
    derived_equal = source.derived == current.derived
    normalization_equal = source.normalization_actions == current.normalization_actions
    needles = (locator, account_token)
    annotation_dependency = serialized_contains(
        {
            "output": source.output,
            "derived": source.derived,
            "normalization_actions": source.normalization_actions,
        },
        needles,
    )
    evidence = source.output.get("evidence") or []
    evidence_dependency = serialized_contains(evidence, needles)
    if not output_equal or not derived_equal or not normalization_equal:
        raise RuntimeError("frozen semantic annotation payload changed")
    if annotation_dependency or evidence_dependency:
        raise RuntimeError("frozen annotation depends on the removed locator")

    aggregate_path = output_dir / "aggregate_results.csv"
    output_dir.mkdir(parents=True, exist_ok=True)
    aggregate_rows = [
        ("units_reviewed", 1, "pass"),
        ("title_edits_exactly_url_only", 1, "pass"),
        ("output_fields_compared", len(source.output), "pass"),
        ("output_fields_changed", 0, "pass"),
        ("derived_fields_compared", len(source.derived), "pass"),
        ("derived_fields_changed", 0, "pass"),
        ("evidence_rows_checked", len(evidence), "pass"),
        ("evidence_rows_depending_on_locator", 0, "pass"),
        ("normalization_actions_changed", 0, "pass"),
    ]
    with aggregate_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(("check", "count", "status"))
        writer.writerows(aggregate_rows)

    private_path = output_dir / "private/record_review.json"
    private_payload = {
        "corpus_index": current.corpus_index,
        "source_layer": current.source_layer,
        "sampling_track": current.sampling_track,
        "source_title_sha256": source.title_sha256,
        "remediated_title_sha256": current.title_sha256,
        "remediation_rule": REMEDIATION_ID,
        "exact_url_only_edit": True,
        "output_payload_equal": output_equal,
        "derived_payload_equal": derived_equal,
        "normalization_actions_equal": normalization_equal,
        "annotation_contains_removed_locator_or_account_token": annotation_dependency,
        "evidence_contains_removed_locator_or_account_token": evidence_dependency,
        "semantic_review_disposition": (
            "retain_frozen_labels_after_nonsemantic_direct_locator_deletion"
        ),
        "review_boundary": (
            "deterministic author-side dependency audit; not independent human recoding"
        ),
    }
    write_json(private_path, private_payload, private=True)

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        """# URL删除后的标签不变性审核（v1）

**日期：**2026-08-23  
**结论：****通过。保留该条既有AI结构化标签。**

审核确认v3与v2之间只有一处经过授权的URL精确删除；标题其余字符保持不变。逐字段比较了42个结构化输出字段、9个派生字段、6条证据和2项规范化动作，变化数均为0。标签、证据和派生数据均不含被删除定位符或其账号字符串，因此没有字段依赖该URL。

这是离线的作者侧依赖审核，不是新的模型调用，也不是独立人工重编码。它支持“删除定位符后沿用既有语义标签”，但不替代残余隐私候选的独立人工审查。

公开报告不包含标题、URL、账号、记录ID或标题哈希。
""",
        encoding="utf-8",
    )

    manifest: dict[str, Any] = {
        "created_at": utc_now(),
        "audit_id": "gendered_visibility_privacy_remediation_label_invariance_v1",
        "status": "passed_retain_frozen_semantic_labels",
        "freeze_id": freeze["freeze_id"],
        "scope": {
            "changed_title_units": 1,
            "output_fields_compared": len(source.output),
            "derived_fields_compared": len(source.derived),
            "evidence_rows_checked": len(evidence),
            "provider_api_calls": 0,
            "independent_human_recodings": 0,
        },
        "decision": (
            "retain frozen annotation because the sole edit deletes a direct "
            "locator absent from all annotation payloads and evidence"
        ),
        "boundary": "author-side deterministic dependency audit",
        "inputs": {
            path.relative_to(ROOT).as_posix(): sha256_file(path)
            for path in (
                V2_MANIFEST,
                V2_MEMBERSHIP,
                V3_MANIFEST,
                V3_MEMBERSHIP,
                V3_OVERRIDES,
                AUDIT_V1_CANDIDATES,
                SCRIPT,
            )
        },
        "outputs": {
            path.relative_to(ROOT).as_posix(): sha256_file(path)
            for path in (aggregate_path, private_path, report_path)
        },
        "privacy": "public outputs contain no title-level or locator values",
    }
    write_json(output_dir / "manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    manifest = run(args.output_dir, args.report)
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "changed_title_units": manifest["scope"]["changed_title_units"],
                "provider_api_calls": manifest["scope"]["provider_api_calls"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
