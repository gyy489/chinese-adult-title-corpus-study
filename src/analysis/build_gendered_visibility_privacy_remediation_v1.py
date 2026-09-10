#!/usr/bin/env python3
"""Build a non-destructive privacy-remediated successor to paper corpus v2.

The sole transformation removes the exact URL confirmed by residual-privacy
audit v1.  The literal locator is read from the mode-0600 audit candidate file;
it is never copied into source code, manifests, reports, or aggregate outputs.
The frozen v2 corpus and all raw/interim provenance remain unchanged.

The result is the researcher-authorized versioned successor layer.  It passes
the narrow direct-locator gate while retaining an explicit pending status for
the independent residual-candidate review.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.analysis.analyze_gendered_visibility_paper_corpus_v1 import (
    Unit,
    load_units,
)
from src.analysis.audit_residual_privacy_paper_corpus_v1 import (
    structured_findings,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = Path(__file__).resolve()
V2_DIR = (
    ROOT
    / "data/processed/gendered_visibility_paper_corpus_v2_text_deduplicated"
)
V2_MANIFEST = V2_DIR / "manifest.json"
V2_MEMBERSHIP = V2_DIR / "private/analytical_corpus_membership.csv"
AUDIT_V1_DIR = ROOT / "data/processed/gendered_visibility_residual_privacy_audit_v1"
AUDIT_V1_MANIFEST = AUDIT_V1_DIR / "manifest.json"
AUDIT_V1_CANDIDATES = AUDIT_V1_DIR / "private/candidate_items.csv"
DEFAULT_OUTPUT_DIR = (
    ROOT
    / "data/processed/gendered_visibility_paper_corpus_v3_privacy_remediated"
)
DEFAULT_REPORT = (
    ROOT
    / "results/logs/gendered-visibility-privacy-remediation-v1/"
    "2026-08-23-report.md"
)
EXPECTED_CORPUS_N = 38_300
REMEDIATION_ID = "remove_confirmed_direct_locator_url_v1"
ASCII_URL_RE = re.compile(
    r"https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+", re.IGNORECASE
)
DIRECT_LOCATOR_CODES = {
    "email_address",
    "url_or_domain",
    "cn_mobile_number",
    "cn_national_id_number",
    "explicit_at_handle",
    "labelled_contact_identifier",
}
PUBLIC_FORBIDDEN_FIELDS = {
    "deidentified_title",
    "remediated_title",
    "title_sha256",
    "source_title_sha256",
    "record_id",
    "source_site",
    "matched_text",
    "url",
    "span_start",
    "span_end",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def title_hash(title: str) -> str:
    return hashlib.sha256(title.encode()).hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(
    path: Path,
    fieldnames: Sequence[str],
    rows: Iterable[dict[str, Any]],
    *,
    private: bool,
) -> None:
    if not private and PUBLIC_FORBIDDEN_FIELDS & set(fieldnames):
        raise RuntimeError("public remediation output contains a private field")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fieldnames, extrasaction="raise", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)
    if private:
        os.chmod(path, 0o600)


def confirmed_url_from_audit(path: Path) -> str:
    rows = [
        row
        for row in read_csv(path)
        if row["automated_disposition"] == "confirmed_direct_locator"
        and row["detector"] == "structured_pattern"
        and row["signal"] == "url_or_domain"
    ]
    if len(rows) != 1:
        raise RuntimeError("expected exactly one confirmed URL candidate")
    matches = ASCII_URL_RE.findall(rows[0]["matched_text"])
    if len(matches) != 1:
        raise RuntimeError("could not isolate exactly one ASCII URL")
    return matches[0]


def remove_exact_url(title: str, url: str) -> str:
    if title.count(url) != 1:
        raise RuntimeError("target title must contain the exact URL once")
    remediated = re.sub(r"\s+", " ", title.replace(url, "", 1)).strip()
    if not remediated or url in remediated:
        raise RuntimeError("URL removal failed or emptied the title")
    return remediated


def direct_locator_counts(units: Sequence[Unit]) -> tuple[Counter[str], set[str]]:
    counts: Counter[str] = Counter()
    title_hashes: set[str] = set()
    for unit in units:
        for finding in structured_findings(unit.title):
            if finding.code in DIRECT_LOCATOR_CODES:
                counts[finding.code] += 1
                title_hashes.add(unit.title_sha256)
    return counts, title_hashes


def build_remediated_units(
    units: Sequence[Unit], url: str
) -> tuple[list[Unit], Unit, Unit]:
    targets = [unit for unit in units if url in unit.title]
    if len(targets) != 1:
        raise RuntimeError("confirmed URL must occur in exactly one corpus title")
    source = targets[0]
    new_title = remove_exact_url(source.title, url)
    remediated = replace(
        source,
        title=new_title,
        title_sha256=title_hash(new_title),
    )
    existing_hashes = {unit.title_sha256 for unit in units}
    if remediated.title_sha256 in existing_hashes:
        raise RuntimeError("remediated title collides with an existing final text")
    updated = [remediated if unit.title_sha256 == source.title_sha256 else unit for unit in units]
    if len({unit.title_sha256 for unit in updated}) != len(updated):
        raise RuntimeError("remediated corpus is not unique by final text")
    return updated, source, remediated


def build_report(manifest: dict[str, Any]) -> str:
    before = manifest["direct_locator_gate"]["before_remediation"]
    after = manifest["direct_locator_gate"]["after_remediation"]
    change = manifest["remediation"]
    return f"""# URL隐私修复报告（v1）

**日期：**2026-08-23  
**源语料：**`gendered_visibility_paper_corpus_v2_text_deduplicated`  
**活跃修复层：**`gendered_visibility_paper_corpus_v3_privacy_remediated`  
**状态：****URL已从目标标题删除，v3已冻结为论文语料；直接定位信息子门禁已通过，独立残余候选审查仍待完成。**

## 完成的变换

- 仅删除了审计v1确认的那一段URL；未删除整条标题，未改写`data/raw/`或冻结v2。
- 修复文本长度减少{change['characters_removed']}个字符；总语料仍为38,300个唯一文本。
- 变更后的标题哈希与另外38,299个文本均不冲突。
- 该条已有AI结构化输出和派生字段中未出现被删URL或账号字符串，本轮未改写语义标签。

## 直接定位信息复测

| 检查 | 修复前 | 修复后 |
|---|---:|---:|
| 含直接定位信息的标题 | {before['title_count']} | {after['title_count']} |
| 直接定位span | {before['span_count']} | {after['span_count']} |

修复后的邮箱、URL／域名、中国大陆手机号、中国大陆身份证号、显式`@handle`和带标签联系值命中合计为0。

## 还不能宣布“完整隐私审计通过”的原因

此次复测只关闭了已确认的硬性漏洞。审计v1中的拉丁姓名／handle长尾候选和600条概率复核队列尚未完成独立审查。因此现在可准确表述为“直接定位信息扫描通过”，不能表述为“已完全匿名”。

## 数据版本边界

新层保存了38,300条哈希成员关系和1条私有文本override。研究者已于2026-08-23指示采用该修复方案；所有依赖`title_sha256`的分析、表图manifest和审计路径必须从v3统一重建。v2保留为不可变历史来源，但不再作为论文的活跃语料。

公开报告不包含被删URL、标题、记录ID、标题哈希或候选字符串。
"""


def run(output_dir: Path, report_path: Path) -> dict[str, Any]:
    v2_manifest = json.loads(V2_MANIFEST.read_text(encoding="utf-8"))
    audit_manifest = json.loads(AUDIT_V1_MANIFEST.read_text(encoding="utf-8"))
    expected_candidate_hash = audit_manifest["outputs"].get(
        AUDIT_V1_CANDIDATES.relative_to(ROOT).as_posix()
    )
    if expected_candidate_hash != sha256_file(AUDIT_V1_CANDIDATES):
        raise RuntimeError("residual-audit candidate checksum mismatch")

    units, _ = load_units(
        freeze_manifest_path=V2_MANIFEST,
        membership_path=V2_MEMBERSHIP,
        expected_corpus_n=EXPECTED_CORPUS_N,
        require_exact_valid_output_membership=False,
    )
    if len(units) != EXPECTED_CORPUS_N:
        raise RuntimeError("v2 corpus count drifted")
    url = confirmed_url_from_audit(AUDIT_V1_CANDIDATES)
    remediated_units, source, remediated = build_remediated_units(units, url)

    serialized_annotation = json.dumps(
        {"output": source.output, "derived": source.derived}, ensure_ascii=False
    )
    account_token = url.rstrip("/").split("/")[-2]
    annotation_contains_removed_locator = (
        url in serialized_annotation or account_token in serialized_annotation
    )
    if annotation_contains_removed_locator:
        raise RuntimeError("annotation payload contains the removed locator")

    before_counts, before_titles = direct_locator_counts(units)
    after_counts, after_titles = direct_locator_counts(remediated_units)
    if sum(before_counts.values()) != 1 or len(before_titles) != 1:
        raise RuntimeError("pre-remediation direct-locator count drifted")
    if sum(after_counts.values()) != 0 or after_titles:
        raise RuntimeError("direct locator remains after remediation")

    v2_membership_rows = read_csv(V2_MEMBERSHIP)
    unit_by_source_hash = {unit.title_sha256: unit for unit in remediated_units}
    membership_rows: list[dict[str, Any]] = []
    for row in v2_membership_rows:
        source_hash = row["title_sha256"]
        current = unit_by_source_hash.get(source_hash)
        if source_hash == source.title_sha256:
            current = remediated
        if current is None:
            raise RuntimeError("could not map v2 membership to remediated unit")
        membership_rows.append(
            {
                "corpus_index": row["corpus_index"],
                "source_layer": row["source_layer"],
                "source_item_index": row["source_item_index"],
                "record_key_sha256": row["record_key_sha256"],
                "title_sha256": current.title_sha256,
                "source_title_sha256": source_hash,
                "record_validity": row["record_validity"],
                "duplicate_text_group_size_in_v1": row[
                    "duplicate_text_group_size_in_v1"
                ],
                "privacy_remediation_applied": (
                    "yes" if source_hash == source.title_sha256 else "no"
                ),
            }
        )
    if len(membership_rows) != EXPECTED_CORPUS_N:
        raise RuntimeError("remediated membership count drifted")
    if len({row["title_sha256"] for row in membership_rows}) != EXPECTED_CORPUS_N:
        raise RuntimeError("remediated membership has duplicate final text hashes")

    private_dir = output_dir / "private"
    membership_path = private_dir / "analytical_corpus_membership.csv"
    write_csv(
        membership_path,
        (
            "corpus_index",
            "source_layer",
            "source_item_index",
            "record_key_sha256",
            "title_sha256",
            "source_title_sha256",
            "record_validity",
            "duplicate_text_group_size_in_v1",
            "privacy_remediation_applied",
        ),
        membership_rows,
        private=True,
    )
    override_path = private_dir / "title_overrides.csv"
    write_csv(
        override_path,
        (
            "corpus_index",
            "source_title_sha256",
            "title_sha256",
            "remediation_rule",
            "remediated_title",
        ),
        (
            {
                "corpus_index": source.corpus_index,
                "source_title_sha256": source.title_sha256,
                "title_sha256": remediated.title_sha256,
                "remediation_rule": REMEDIATION_ID,
                "remediated_title": remediated.title,
            },
        ),
        private=True,
    )

    direct_rows = [
        {
            "test_code": code,
            "before_span_count": before_counts.get(code, 0),
            "after_span_count": after_counts.get(code, 0),
            "status": "passed_zero_after_remediation",
        }
        for code in sorted(DIRECT_LOCATOR_CODES)
    ]
    direct_audit_path = output_dir / "direct_locator_audit.csv"
    write_csv(
        direct_audit_path,
        ("test_code", "before_span_count", "after_span_count", "status"),
        direct_rows,
        private=False,
    )

    track_counts = Counter(unit.sampling_track for unit in remediated_units)
    manifest: dict[str, Any] = {
        "created_at": utc_now(),
        "freeze_id": "gendered_visibility_paper_corpus_v3_privacy_remediated",
        "status": (
            "frozen_active_privacy_remediated_direct_locator_gate_passed_"
            "pending_independent_residual_review"
        ),
        "decision_date": "2026-08-23",
        "supersedes_for_manuscript_analysis": (
            "gendered_visibility_paper_corpus_v2_text_deduplicated"
        ),
        "selection_rule": v2_manifest["selection_rule"],
        "scope": {
            **v2_manifest["scope"],
            "unique_final_deidentified_title_texts": len(remediated_units),
            "sampling_tracks": dict(sorted(track_counts.items())),
            "units_changed": 1,
            "units_removed": 0,
            "raw_data_files_read": 0,
            "provider_api_calls": 0,
            "frozen_source_outputs_rewritten": 0,
        },
        "remediation": {
            "remediation_id": REMEDIATION_ID,
            "decision_date": "2026-08-23",
            "removed_value_class": "literal_account_or_page_url",
            "characters_removed": len(source.title) - len(remediated.title),
            "source_title_length": len(source.title),
            "remediated_title_length": len(remediated.title),
            "new_title_hash_collision": False,
            "annotation_payload_contains_removed_locator": False,
            "semantic_annotation_rewritten": False,
        },
        "direct_locator_gate": {
            "before_remediation": {
                "span_count": sum(before_counts.values()),
                "title_count": len(before_titles),
                "by_type": {
                    code: before_counts.get(code, 0)
                    for code in sorted(DIRECT_LOCATOR_CODES)
                },
            },
            "after_remediation": {
                "span_count": sum(after_counts.values()),
                "title_count": len(after_titles),
                "by_type": {
                    code: after_counts.get(code, 0)
                    for code in sorted(DIRECT_LOCATOR_CODES)
                },
                "status": "passed_zero_direct_locators",
            },
        },
        "full_residual_privacy_gate": {
            "status": "pending_independent_probability_and_latin_handle_review",
            "reason": (
                "Removing the confirmed URL closes the direct-locator finding but "
                "does not adjudicate the audit-v1 long-tail candidate queues."
            ),
        },
        "manuscript_activation": {
            "status": "active_frozen_by_researcher_instruction_2026_08_23",
            "active_corpus": "gendered_visibility_paper_corpus_v3_privacy_remediated",
            "downstream_rule": "rebuild_all_title_hash_dependent_outputs_from_v3",
        },
        "counts": v2_manifest["counts"],
        "privacy_controls": {
            "literal_removed_url_in_public_outputs": False,
            "title_override_mode": oct(override_path.stat().st_mode & 0o777),
            "membership_mode": oct(membership_path.stat().st_mode & 0o777),
        },
        "inputs": {
            path.relative_to(ROOT).as_posix(): sha256_file(path)
            for path in (
                V2_MANIFEST,
                V2_MEMBERSHIP,
                AUDIT_V1_MANIFEST,
                AUDIT_V1_CANDIDATES,
                SCRIPT,
            )
        },
        "outputs": {},
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(build_report(manifest), encoding="utf-8")
    manifest_path = output_dir / "manifest.json"
    manifest["outputs"] = {
        path.relative_to(ROOT).as_posix(): sha256_file(path)
        for path in (
            membership_path,
            override_path,
            direct_audit_path,
            report_path,
        )
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    manifest = run(args.output_dir, args.report)
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "corpus_n": manifest["scope"][
                    "unique_final_deidentified_title_texts"
                ],
                "units_changed": manifest["scope"]["units_changed"],
                "direct_locator_spans_after": manifest["direct_locator_gate"][
                    "after_remediation"
                ]["span_count"],
                "output_dir": args.output_dir.relative_to(ROOT).as_posix(),
                "report": args.report.relative_to(ROOT).as_posix(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
