#!/usr/bin/env python3
"""Freeze the checked activation decision for the privacy-remediated v4 corpus."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = Path(__file__).resolve()
V4_MANIFEST = (
    ROOT
    / "data/processed/gendered_visibility_paper_corpus_v4_privacy_remediated/manifest.json"
)
ADJUDICATION_MANIFEST = (
    ROOT / "data/processed/gendered_visibility_privacy_span_adjudication_v1/manifest.json"
)
LABEL_AUDIT_MANIFEST = (
    ROOT / "data/processed/gendered_visibility_privacy_label_dependency_v2/manifest.json"
)
RESIDUAL_AUDIT_MANIFEST = (
    ROOT / "data/processed/gendered_visibility_residual_privacy_audit_v3/manifest.json"
)
OUTPUT_DIR = ROOT / "data/processed/gendered_visibility_privacy_closure_v2"
REPORT_PATH = (
    ROOT
    / "results/logs/gendered-visibility-privacy-closure-v2/2026-08-23-report.md"
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_aggregate(path: Path) -> None:
    rows = [
        ("active_unique_final_texts", 38_298),
        ("probability_track_texts", 36_303),
        ("mechanism_enriched_texts", 1_995),
        ("researcher_confirmed_titles", 111),
        ("redacted_titles", 100),
        ("retained_catalog_number_titles", 11),
        ("excluded_titles", 0),
        ("post_remediation_duplicate_contributions_removed", 2),
        ("postbuild_direct_locator_spans", 0),
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("metric", "count"))
        writer.writerows(rows)


def build_report() -> str:
    return """# v4隐私修复关闭与启用报告

2026年8月23日，研究者完成111条确认标题的最终处置：100条执行132处精确片段替换，
11条目录编号保留，0条整条排除。修复后重新按最终文本哈希去重，两个重复组各移除一条
额外贡献，形成38,298个唯一最终去标识标题文本（36,303个概率轨、1,995个机制加量轨）。

后构建全量扫描的URL、电子邮件、中国大陆手机号、中国大陆身份证号、显式`@handle`和
带标签联系标识符均为0。主要候选指标、位置分布、敏感性范围和授权边界结果未改变。
因此v4从本报告冻结时起成为论文活跃语料，v3保留为不可变历史前身。

该关闭只表示研究者确认项已修复且自动直接定位门禁通过，不构成完全匿名或独立匿名性
认证。私有AI修复前字面证据只保留作不可变溯源，不得进入发布文件或投稿包；新生成的
600条概率样本尚未被独立裁决。
"""


def run(output_dir: Path = OUTPUT_DIR, report_path: Path = REPORT_PATH) -> dict[str, Any]:
    v4 = load(V4_MANIFEST)
    adjudication = load(ADJUDICATION_MANIFEST)
    label_audit = load(LABEL_AUDIT_MANIFEST)
    residual = load(RESIDUAL_AUDIT_MANIFEST)
    if v4["scope"]["paper_analytical_corpus_unique_final_texts"] != 38_298:
        raise RuntimeError("v4 corpus count drifted")
    if v4["scope"]["sampling_tracks"] != {"mechanism": 1_995, "probability": 36_303}:
        raise RuntimeError("v4 sampling-track counts drifted")
    if adjudication["status"] != "complete_frozen_researcher_adjudication":
        raise RuntimeError("researcher adjudication is not frozen")
    if label_audit["status"] != (
        "passed_for_aggregate_label_reuse_with_private_literal_evidence_release_prohibition"
    ):
        raise RuntimeError("label-dependency audit did not pass")
    if not residual["status"].startswith("passed_postbuild_direct_locator_scan"):
        raise RuntimeError("postbuild residual audit did not pass")
    if residual["counts"]["confirmed_direct_locator_spans"] != 0:
        raise RuntimeError("postbuild direct-locator findings remain")

    output_dir.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    aggregate_path = output_dir / "aggregate_results.csv"
    write_aggregate(aggregate_path)
    report_path.write_text(build_report(), encoding="utf-8")
    inputs = (V4_MANIFEST, ADJUDICATION_MANIFEST, LABEL_AUDIT_MANIFEST, RESIDUAL_AUDIT_MANIFEST, SCRIPT)
    manifest = {
        "created_at": utc_now(),
        "closure_id": "gendered_visibility_privacy_closure_v2",
        "status": "complete_v4_active_for_manuscript_analysis",
        "active_corpus": v4["freeze_id"],
        "superseded_corpus": "gendered_visibility_paper_corpus_v3_privacy_remediated",
        "counts": {
            "unique_final_texts": 38_298,
            "sampling_tracks": {"mechanism": 1_995, "probability": 36_303},
            "confirmed_titles": 111,
            "redacted_titles": 100,
            "retained_titles": 11,
            "excluded_titles": 0,
            "postbuild_direct_locator_spans": 0,
        },
        "claims": {
            "researcher_remediation_complete": True,
            "automated_direct_locator_gate_passed": True,
            "independent_anonymity_certification": False,
            "complete_anonymity_claim_allowed": False,
            "private_literal_ai_evidence_release_allowed": False,
        },
        "inputs": {relative(path): sha256_file(path) for path in inputs},
        "outputs": {
            relative(aggregate_path): sha256_file(aggregate_path),
            relative(report_path): sha256_file(report_path),
        },
        "raw_data_files_read": 0,
        "provider_api_calls": 0,
        "frozen_outputs_rewritten": 0,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
