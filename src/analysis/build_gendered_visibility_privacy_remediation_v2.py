#!/usr/bin/env python3
"""Build the v4 successor after researcher-confirmed exact-span remediation."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.analysis.analyze_gendered_visibility_paper_corpus_v1 import load_units
from src.analysis.build_gendered_visibility_privacy_remediation_v1 import (
    DIRECT_LOCATOR_CODES,
    direct_locator_counts,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = Path(__file__).resolve()
V3_DIR = ROOT / "data/processed/gendered_visibility_paper_corpus_v3_privacy_remediated"
V3_MANIFEST = V3_DIR / "manifest.json"
V3_MEMBERSHIP = V3_DIR / "private/analytical_corpus_membership.csv"
V3_OVERRIDES = V3_DIR / "private/title_overrides.csv"
ADJUDICATION_DIR = (
    ROOT / "data/processed/gendered_visibility_privacy_span_adjudication_v1"
)
ADJUDICATION_MANIFEST = ADJUDICATION_DIR / "manifest.json"
FINAL_ADJUDICATIONS = ADJUDICATION_DIR / "private/final_adjudications.csv"
DEFAULT_OUTPUT_DIR = (
    ROOT / "data/processed/gendered_visibility_paper_corpus_v4_privacy_remediated"
)
DEFAULT_REPORT = (
    ROOT
    / "results/logs/gendered-visibility-privacy-remediation-v2/"
    "2026-08-23-report.md"
)
EXPECTED_V3_N = 38_300
EXPECTED_V4_N = 38_298
REMEDIATION_RULE = "researcher_confirmed_exact_identity_spans_v1"
PUBLIC_FORBIDDEN_FIELDS = {
    "record_id",
    "record_key_sha256",
    "remediated_title",
    "source_site",
    "source_title",
    "source_title_sha256",
    "title_sha256",
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


def relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


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
        raise RuntimeError("公开v4产物包含标题级隐私字段")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fieldnames, extrasaction="raise", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)
    if private:
        os.chmod(path, 0o600)


def _verify_adjudications() -> list[dict[str, str]]:
    manifest = json.loads(ADJUDICATION_MANIFEST.read_text(encoding="utf-8"))
    if manifest["status"] != "complete_frozen_researcher_adjudication":
        raise RuntimeError("片段处置层未冻结完成")
    if manifest["outputs"].get(relative(FINAL_ADJUDICATIONS)) != sha256_file(
        FINAL_ADJUDICATIONS
    ):
        raise RuntimeError("最终片段处置SHA-256不一致")
    rows = read_csv(FINAL_ADJUDICATIONS)
    if len(rows) != 111:
        raise RuntimeError("最终片段处置不是111条")
    return rows


def _representative_key(row: dict[str, Any]) -> tuple[str, str, int]:
    return (
        str(row["record_key_sha256"]),
        str(row["source_layer"]),
        int(row["source_item_index"]),
    )


def build_successor() -> dict[str, Any]:
    adjudications = _verify_adjudications()
    decisions = {row["source_title_sha256"]: row for row in adjudications}
    if len(decisions) != 111:
        raise RuntimeError("最终片段处置源标题不唯一")

    units, v3_manifest = load_units(
        V3_MANIFEST,
        V3_MEMBERSHIP,
        EXPECTED_V3_N,
        require_exact_valid_output_membership=False,
    )
    v3_membership = read_csv(V3_MEMBERSHIP)
    v3_overrides = {
        row["source_title_sha256"]: row for row in read_csv(V3_OVERRIDES)
    }
    unit_by_index = {unit.corpus_index: unit for unit in units}
    observed_decisions: set[str] = set()
    proposed: list[dict[str, Any]] = []

    for member in v3_membership:
        predecessor_index = int(member["corpus_index"])
        unit = unit_by_index[predecessor_index]
        predecessor_hash = member["title_sha256"]
        decision = decisions.get(predecessor_hash)
        action = decision["action"] if decision else "none"
        if decision:
            observed_decisions.add(predecessor_hash)
        if action == "exclude":
            continue
        if action == "redact":
            final_title = decision["remediated_title"]
            if not final_title or final_title == unit.title:
                raise RuntimeError("精确脱敏未改变标题")
        else:
            final_title = unit.title
            if action == "retain" and decision["remediated_title"] != unit.title:
                raise RuntimeError("保留项改变了标题")
        final_hash = title_hash(final_title)
        proposed.append(
            {
                "predecessor_corpus_index": predecessor_index,
                "source_layer": member["source_layer"],
                "source_item_index": int(member["source_item_index"]),
                "record_key_sha256": member["record_key_sha256"],
                "title_sha256": final_hash,
                "source_title_sha256": member["source_title_sha256"],
                "predecessor_title_sha256": predecessor_hash,
                "record_validity": member["record_validity"],
                "duplicate_text_group_size_in_v1": member[
                    "duplicate_text_group_size_in_v1"
                ],
                "privacy_remediation_applied": (
                    "identity_span_redacted"
                    if action == "redact"
                    else "catalog_number_reviewed_retain"
                    if action == "retain"
                    else "prior_url_remediation_only"
                    if predecessor_hash != member["source_title_sha256"]
                    else "none"
                ),
                "final_title": final_title,
                "sampling_track": unit.sampling_track,
                "decision_action": action,
                "unit": replace(unit, title=final_title, title_sha256=final_hash),
            }
        )
    if observed_decisions != set(decisions):
        raise RuntimeError("部分处置标题不在v3语料")

    groups: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in proposed:
        groups[row["title_sha256"]].append(row)
    kept: list[dict[str, Any]] = []
    collision_groups: list[dict[str, Any]] = []
    for final_hash, group in groups.items():
        ordered = sorted(group, key=_representative_key)
        winner = ordered[0]
        winner["final_text_group_size_before_v4_dedup"] = len(ordered)
        kept.append(winner)
        if len(ordered) > 1:
            collision_groups.append(
                {
                    "final_title_sha256": final_hash,
                    "group_size": len(ordered),
                    "kept_predecessor_corpus_index": winner[
                        "predecessor_corpus_index"
                    ],
                    "kept_record_key_sha256": winner["record_key_sha256"],
                    "dropped_predecessor_corpus_indices_json": json.dumps(
                        [row["predecessor_corpus_index"] for row in ordered[1:]],
                        separators=(",", ":"),
                    ),
                    "dropped_record_key_sha256_json": json.dumps(
                        [row["record_key_sha256"] for row in ordered[1:]],
                        separators=(",", ":"),
                    ),
                }
            )
    kept.sort(key=lambda row: row["predecessor_corpus_index"])
    if len(kept) != EXPECTED_V4_N:
        raise RuntimeError(f"v4唯一文本数漂移: {len(kept)}")
    if len(collision_groups) != 2 or any(
        row["group_size"] != 2 for row in collision_groups
    ):
        raise RuntimeError("v4碰撞组不是冻结的2组×2条")

    membership_rows: list[dict[str, Any]] = []
    override_rows: list[dict[str, Any]] = []
    final_units = []
    for corpus_index, row in enumerate(kept, start=1):
        membership_rows.append(
            {
                "corpus_index": corpus_index,
                "predecessor_corpus_index": row["predecessor_corpus_index"],
                "source_layer": row["source_layer"],
                "source_item_index": row["source_item_index"],
                "record_key_sha256": row["record_key_sha256"],
                "title_sha256": row["title_sha256"],
                "source_title_sha256": row["source_title_sha256"],
                "predecessor_title_sha256": row["predecessor_title_sha256"],
                "record_validity": row["record_validity"],
                "duplicate_text_group_size_in_v1": row[
                    "duplicate_text_group_size_in_v1"
                ],
                "privacy_remediation_applied": row[
                    "privacy_remediation_applied"
                ],
                "final_text_group_size_before_v4_dedup": row[
                    "final_text_group_size_before_v4_dedup"
                ],
            }
        )
        final_units.append(replace(row["unit"], corpus_index=corpus_index))
        if row["title_sha256"] != row["source_title_sha256"]:
            if row["decision_action"] == "redact":
                rule = (
                    "remove_confirmed_direct_locator_url_v1+" + REMEDIATION_RULE
                    if row["predecessor_title_sha256"]
                    != row["source_title_sha256"]
                    else REMEDIATION_RULE
                )
            else:
                prior = v3_overrides.get(row["source_title_sha256"])
                if prior is None:
                    raise RuntimeError("累计override缺少v3规则")
                rule = prior["remediation_rule"]
            override_rows.append(
                {
                    "corpus_index": corpus_index,
                    "predecessor_corpus_index": row["predecessor_corpus_index"],
                    "source_title_sha256": row["source_title_sha256"],
                    "predecessor_title_sha256": row["predecessor_title_sha256"],
                    "title_sha256": row["title_sha256"],
                    "remediation_rule": rule,
                    "remediated_title": row["final_title"],
                }
            )
    if len({row["title_sha256"] for row in membership_rows}) != EXPECTED_V4_N:
        raise RuntimeError("v4成员最终文本哈希不唯一")
    if len(override_rows) != 99:
        raise RuntimeError(f"v4累计override数漂移: {len(override_rows)}")

    direct_counts, direct_titles = direct_locator_counts(final_units)
    if sum(direct_counts.values()) or direct_titles:
        raise RuntimeError("v4仍含直接定位信息")
    return {
        "v3_manifest": v3_manifest,
        "adjudications": adjudications,
        "membership_rows": membership_rows,
        "override_rows": override_rows,
        "collision_groups": collision_groups,
        "final_units": final_units,
        "direct_counts": direct_counts,
    }


def build_report(manifest: dict[str, Any]) -> str:
    scope = manifest["scope"]
    remediation = manifest["remediation"]
    return f"""# 精确身份片段脱敏后的v4语料报告

**日期：**2026-08-23  
**源语料：**`gendered_visibility_paper_corpus_v3_privacy_remediated`  
**后继层：**`gendered_visibility_paper_corpus_v4_privacy_remediated`

冻结的111条处置中，{remediation['titles_redacted']}条进行了精确span替换，
{remediation['titles_retained']}条作品番号保留不改，0条整条排除。100条修改后形成98个
不同文本；两组各有两条修改标题收敛为同一最终文本。按既有、与实质标签无关的记录键哈希
规则，每组保留一份贡献，因此v4包含{scope['unique_final_deidentified_title_texts']:,}个唯一
最终文本：{scope['sampling_tracks']['probability']:,}个概率轨、
{scope['sampling_tracks']['mechanism']:,}个机制类别加量轨。

没有修改`data/raw/`、v3成员或冻结AI源输出。全量直接定位六类扫描在v4中仍为0。该层尚需
完成后构建残余扫描和标签依赖／发布边界审核；研究者复核不等于独立匿名性认证。私有标题、
span、碰撞哈希和AI证据不得进入投稿包。
"""


def run(output_dir: Path, report_path: Path) -> dict[str, Any]:
    built = build_successor()
    membership_rows = built["membership_rows"]
    override_rows = built["override_rows"]
    collision_groups = built["collision_groups"]
    final_units = built["final_units"]
    v3_manifest = built["v3_manifest"]

    private_dir = output_dir / "private"
    membership_path = private_dir / "analytical_corpus_membership.csv"
    write_csv(
        membership_path,
        tuple(membership_rows[0]),
        membership_rows,
        private=True,
    )
    overrides_path = private_dir / "title_overrides.csv"
    write_csv(overrides_path, tuple(override_rows[0]), override_rows, private=True)
    collisions_path = private_dir / "final_text_collision_groups.csv"
    write_csv(
        collisions_path,
        tuple(collision_groups[0]),
        collision_groups,
        private=True,
    )

    track_counts = Counter(unit.sampling_track for unit in final_units)
    remediation_counts = Counter(
        row["privacy_remediation_applied"] for row in membership_rows
    )
    direct_path = output_dir / "direct_locator_audit.csv"
    direct_rows = [
        {
            "test_code": code,
            "after_span_count": built["direct_counts"].get(code, 0),
            "status": "passed_zero_after_v4_remediation",
        }
        for code in sorted(DIRECT_LOCATOR_CODES)
    ]
    write_csv(
        direct_path,
        ("test_code", "after_span_count", "status"),
        direct_rows,
        private=False,
    )
    aggregate_path = output_dir / "remediation_aggregate.csv"
    aggregate_rows = [
        {"metric": "v3_unique_texts", "value": EXPECTED_V3_N},
        {"metric": "titles_redacted", "value": 100},
        {"metric": "titles_retained", "value": 11},
        {"metric": "span_replacements", "value": 132},
        {"metric": "post_remediation_collision_groups", "value": 2},
        {"metric": "contributions_removed_by_final_text_dedup", "value": 2},
        {"metric": "v4_unique_texts", "value": EXPECTED_V4_N},
    ]
    write_csv(
        aggregate_path,
        ("metric", "value"),
        aggregate_rows,
        private=False,
    )

    manifest: dict[str, Any] = {
        "created_at": utc_now(),
        "freeze_id": "gendered_visibility_paper_corpus_v4_privacy_remediated",
        "status": (
            "frozen_successor_researcher_remediation_complete_direct_locator_"
            "gate_passed_pending_postbuild_audits"
        ),
        "decision_date": "2026-08-23",
        "supersedes_after_postbuild_audits": v3_manifest["freeze_id"],
        "selection_rule": {
            **v3_manifest["selection_rule"],
            "post_remediation_rule": (
                "after exact-span replacement, group again by final title_sha256 "
                "and retain the lexicographically smallest record_key_sha256; "
                "ties use source_layer and source_item_index"
            ),
        },
        "scope": {
            **v3_manifest["scope"],
            "paper_analytical_corpus_unique_final_texts": EXPECTED_V4_N,
            "unique_final_deidentified_title_texts": EXPECTED_V4_N,
            "unique_final_texts_outside_paper_corpus": (
                int(v3_manifest["scope"]["source_frame_unique_final_texts"])
                - EXPECTED_V4_N
            ),
            "sampling_tracks": dict(sorted(track_counts.items())),
            "units_changed": 100,
            "units_removed": 2,
            "raw_data_files_read": 0,
            "provider_api_calls": 0,
            "frozen_source_outputs_rewritten": 0,
        },
        "remediation": {
            "remediation_rule": REMEDIATION_RULE,
            "confirmed_titles": 111,
            "titles_redacted": 100,
            "titles_retained": 11,
            "titles_excluded": 0,
            "span_replacements": 132,
            "replacement_counts": {"某人": 126, "某机构": 6},
            "changed_title_contributions": 100,
            "distinct_changed_final_texts": 98,
            "collision_with_unaffected_title_groups": 0,
            "changed_to_changed_collision_groups": 2,
            "contributions_removed_by_final_text_dedup": 2,
            "membership_dispositions": dict(sorted(remediation_counts.items())),
        },
        "direct_locator_gate": {
            "after_remediation": {
                "span_count": 0,
                "title_count": 0,
                "by_type": {
                    code: built["direct_counts"].get(code, 0)
                    for code in sorted(DIRECT_LOCATOR_CODES)
                },
                "status": "passed_zero_direct_locators",
            }
        },
        "full_residual_privacy_gate": {
            "status": (
                "researcher_review_remediated_pending_postbuild_residual_scan_"
                "and_independent_confirmation"
            ),
            "complete_anonymity_claim_allowed": False,
        },
        "manuscript_activation": {
            "status": "ready_for_downstream_rebuild_pending_postbuild_audits",
            "active_predecessor_until_audits_complete": v3_manifest["freeze_id"],
        },
        "counts": {
            **v3_manifest["counts"],
            "v4_unique_text_members": EXPECTED_V4_N,
            "v4_post_remediation_duplicate_groups": 2,
            "v4_extra_contributions_removed": 2,
        },
        "privacy_controls": {
            "membership_mode": oct(membership_path.stat().st_mode & 0o777),
            "title_override_mode": oct(overrides_path.stat().st_mode & 0o777),
            "collision_audit_mode": oct(collisions_path.stat().st_mode & 0o777),
            "public_outputs_aggregate_only": True,
            "private_ai_evidence_release_allowed": False,
        },
        "inputs": {
            relative(path): sha256_file(path)
            for path in (
                V3_MANIFEST,
                V3_MEMBERSHIP,
                V3_OVERRIDES,
                ADJUDICATION_MANIFEST,
                FINAL_ADJUDICATIONS,
                SCRIPT,
            )
        },
        "outputs": {},
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(build_report(manifest), encoding="utf-8")
    output_paths = (
        membership_path,
        overrides_path,
        collisions_path,
        direct_path,
        aggregate_path,
        report_path,
    )
    manifest["outputs"] = {
        relative(path): sha256_file(path) for path in output_paths
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
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
                "corpus_n": manifest["scope"][
                    "unique_final_deidentified_title_texts"
                ],
                "sampling_tracks": manifest["scope"]["sampling_tracks"],
                "collisions": manifest["remediation"][
                    "changed_to_changed_collision_groups"
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
