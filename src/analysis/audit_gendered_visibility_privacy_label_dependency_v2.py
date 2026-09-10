#!/usr/bin/env python3
"""Audit label reuse and private evidence boundaries after v4 name redaction."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.analysis.analyze_gendered_visibility_paper_corpus_v1 import load_units

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = Path(__file__).resolve()
V3_DIR = ROOT / "data/processed/gendered_visibility_paper_corpus_v3_privacy_remediated"
V3_MANIFEST = V3_DIR / "manifest.json"
V3_MEMBERSHIP = V3_DIR / "private/analytical_corpus_membership.csv"
V4_DIR = ROOT / "data/processed/gendered_visibility_paper_corpus_v4_privacy_remediated"
V4_MANIFEST = V4_DIR / "manifest.json"
V4_MEMBERSHIP = V4_DIR / "private/analytical_corpus_membership.csv"
ADJUDICATION_DIR = (
    ROOT / "data/processed/gendered_visibility_privacy_span_adjudication_v1"
)
ADJUDICATION_MANIFEST = ADJUDICATION_DIR / "manifest.json"
FINAL_ADJUDICATIONS = ADJUDICATION_DIR / "private/final_adjudications.csv"
DEFAULT_OUTPUT_DIR = (
    ROOT / "data/processed/gendered_visibility_privacy_label_dependency_v2"
)
DEFAULT_REPORT = (
    ROOT
    / "results/logs/gendered-visibility-privacy-label-dependency-v2/"
    "2026-08-23-report.md"
)
EXPECTED_CHANGED = 100
LITERAL_EVIDENCE_FIELDS = {"evidence", "adjudication_reasons"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def contains_any(value: Any, needles: list[str]) -> bool:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return any(needle and needle in serialized for needle in needles)


def write_csv(
    path: Path,
    fieldnames: Sequence[str],
    rows: Iterable[dict[str, Any]],
    *,
    private: bool,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fieldnames, extrasaction="raise", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)
    if private:
        os.chmod(path, 0o600)


def audit_rows() -> tuple[list[dict[str, Any]], dict[str, int]]:
    decisions = [
        row for row in read_csv(FINAL_ADJUDICATIONS) if row["action"] == "redact"
    ]
    if len(decisions) != EXPECTED_CHANGED:
        raise RuntimeError("冻结处置中的修改标题不是100条")
    v3_units, _ = load_units(
        V3_MANIFEST,
        V3_MEMBERSHIP,
        38_300,
        require_exact_valid_output_membership=False,
    )
    v4_units, _ = load_units(
        V4_MANIFEST,
        V4_MEMBERSHIP,
        38_298,
        require_exact_valid_output_membership=False,
    )
    v3_by_hash = {unit.title_sha256: unit for unit in v3_units}
    v4_members = read_csv(V4_MEMBERSHIP)
    v4_by_predecessor_index = {
        int(row["predecessor_corpus_index"]): row for row in v4_members
    }
    v3_members = {
        row["title_sha256"]: row for row in read_csv(V3_MEMBERSHIP)
    }
    v4_unit_by_index = {unit.corpus_index: unit for unit in v4_units}

    rows: list[dict[str, Any]] = []
    totals = Counter()
    for decision in decisions:
        source_hash = decision["source_title_sha256"]
        source = v3_by_hash[source_hash]
        source_member = v3_members[source_hash]
        predecessor_index = int(source_member["corpus_index"])
        current_member = v4_by_predecessor_index.get(predecessor_index)
        spans = json.loads(decision["spans_json"])
        needles = [span["text"] for span in spans]

        evidence_hit = contains_any(source.output.get("evidence") or [], needles)
        reason_hit = contains_any(
            source.output.get("adjudication_reasons") or [], needles
        )
        other_output = {
            key: value
            for key, value in source.output.items()
            if key not in LITERAL_EVIDENCE_FIELDS
        }
        other_output_hit = contains_any(other_output, needles)
        derived_hit = contains_any(source.derived, needles)
        normalization_hit = contains_any(source.normalization_actions, needles)
        if other_output_hit or derived_hit or normalization_hit:
            raise RuntimeError("身份片段进入分类标签、派生字段或规范化动作")

        retained = current_member is not None
        categorical_equal = False
        if retained:
            current = v4_unit_by_index[int(current_member["corpus_index"])]
            categorical_equal = all(
                source.output.get(key) == current.output.get(key)
                for key in set(source.output) - LITERAL_EVIDENCE_FIELDS
            )
            if not categorical_equal or source.derived != current.derived:
                raise RuntimeError("v4保留成员的分类或派生标签发生变化")
        totals["changed_source_contributions"] += 1
        totals["retained_after_final_text_dedup"] += retained
        totals["dropped_after_final_text_dedup"] += not retained
        totals["literal_evidence_hit_units"] += evidence_hit
        totals["literal_adjudication_reason_hit_units"] += reason_hit
        totals["other_categorical_literal_hit_units"] += other_output_hit
        totals["derived_literal_hit_units"] += derived_hit
        totals["normalization_literal_hit_units"] += normalization_hit
        totals["retained_categorical_payload_equal_units"] += (
            retained and categorical_equal
        )
        rows.append(
            {
                "source_title_sha256": source_hash,
                "predecessor_corpus_index": predecessor_index,
                "retained_after_final_text_dedup": str(retained).lower(),
                "selected_span_count": len(spans),
                "literal_evidence_hit": str(evidence_hit).lower(),
                "literal_adjudication_reason_hit": str(reason_hit).lower(),
                "other_categorical_literal_hit": str(other_output_hit).lower(),
                "derived_literal_hit": str(derived_hit).lower(),
                "normalization_literal_hit": str(normalization_hit).lower(),
                "retained_categorical_payload_equal": str(categorical_equal).lower(),
                "release_disposition": (
                    "retain_categorical_labels_exclude_private_literal_evidence"
                ),
            }
        )
    expected = {
        "changed_source_contributions": 100,
        "retained_after_final_text_dedup": 98,
        "dropped_after_final_text_dedup": 2,
        "literal_evidence_hit_units": 70,
        "literal_adjudication_reason_hit_units": 1,
        "other_categorical_literal_hit_units": 0,
        "derived_literal_hit_units": 0,
        "normalization_literal_hit_units": 0,
        "retained_categorical_payload_equal_units": 98,
    }
    if dict(totals) != expected:
        raise RuntimeError(f"标签依赖审核计数漂移: {dict(totals)}")
    return rows, expected


def report_text(counts: dict[str, int]) -> str:
    return f"""# v4身份片段修复后的标签依赖与发布边界审核

**日期：**2026-08-23  
**结论：****允许复用分类标签进行汇总分析，但禁止发布私有字面证据；本审核不声称姓名替换前后语义标签完全不变。**

100个修改来源贡献中，98个在最终文本重新去重后保留，2个按记录键哈希规则移除。保留的
98个贡献的分类字段与派生字段逐项保持原值。被替换身份片段没有进入分类值、派生字段或
规范化动作。

但是，{counts['literal_evidence_hit_units']}个来源贡献的私有`evidence`原文、
{counts['literal_adjudication_reason_hit_units']}个来源贡献的私有裁决理由仍逐字包含被遮蔽片段。
这些字段属于不可变编码溯源，不复制到v4目录、公开汇总、表图或投稿包。论文方法必须如实说明
残余身份修复发生在AI编码之后；因此这里的决定是“复用类别、隔离字面证据”，不是把本轮
描述成重新编码、独立复核或完整匿名性认证。

本审核离线完成，供应商/API调用为0，`data/raw/`和冻结AI响应均未改写。
"""


def run(output_dir: Path, report_path: Path) -> dict[str, Any]:
    rows, counts = audit_rows()
    private_path = output_dir / "private/record_audit.csv"
    write_csv(private_path, tuple(rows[0]), rows, private=True)
    aggregate_path = output_dir / "aggregate_results.csv"
    aggregate_rows = [
        {"metric": key, "value": value, "status": "pass"}
        for key, value in counts.items()
    ]
    write_csv(
        aggregate_path,
        ("metric", "value", "status"),
        aggregate_rows,
        private=False,
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report_text(counts), encoding="utf-8")

    inputs = (
        V3_MANIFEST,
        V3_MEMBERSHIP,
        V4_MANIFEST,
        V4_MEMBERSHIP,
        ADJUDICATION_MANIFEST,
        FINAL_ADJUDICATIONS,
        SCRIPT,
    )
    outputs = (private_path, aggregate_path, report_path)
    manifest = {
        "created_at": utc_now(),
        "audit_id": "gendered_visibility_privacy_label_dependency_v2",
        "status": (
            "passed_for_aggregate_label_reuse_with_private_literal_evidence_"
            "release_prohibition"
        ),
        "freeze_id": "gendered_visibility_paper_corpus_v4_privacy_remediated",
        "counts": counts,
        "decision": (
            "retain categorical and derived labels for aggregate analysis; do not "
            "release private literal evidence or adjudication reasons"
        ),
        "boundary": (
            "post-coding residual identity remediation; not semantic invariance, "
            "new recoding, independent privacy review, or anonymity certification"
        ),
        "privacy_controls": {
            "private_record_audit_mode": oct(private_path.stat().st_mode & 0o777),
            "literal_evidence_copied_to_v4_outputs": False,
            "literal_evidence_release_allowed": False,
            "provider_api_calls": 0,
            "raw_data_files_read": 0,
            "frozen_ai_outputs_rewritten": 0,
        },
        "inputs": {relative(path): sha256_file(path) for path in inputs},
        "outputs": {relative(path): sha256_file(path) for path in outputs},
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    manifest = run(args.output_dir, args.report)
    print(json.dumps(manifest["counts"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
