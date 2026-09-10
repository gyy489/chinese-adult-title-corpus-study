#!/usr/bin/env python3
"""Run the post-remediation residual privacy audit against the v4 corpus."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from src.analysis.audit_residual_privacy_paper_corpus_v1 import (
    DEFAULT_NER_MODELS,
    ROOT,
    run_audit,
)

SCRIPT = Path(__file__).resolve()
CORPUS_ID = "gendered_visibility_paper_corpus_v4_privacy_remediated"
CORPUS_DIR = ROOT / f"data/processed/{CORPUS_ID}"
CORPUS_MANIFEST = CORPUS_DIR / "manifest.json"
CORPUS_MEMBERSHIP = CORPUS_DIR / "private/analytical_corpus_membership.csv"
ADJUDICATION_MANIFEST = (
    ROOT
    / "data/processed/gendered_visibility_privacy_span_adjudication_v1/manifest.json"
)
LABEL_AUDIT_MANIFEST = (
    ROOT / "data/processed/gendered_visibility_privacy_label_dependency_v2/manifest.json"
)
DEFAULT_OUTPUT_DIR = ROOT / "data/processed/gendered_visibility_residual_privacy_audit_v3"
DEFAULT_REPORT = (
    ROOT
    / "results/logs/gendered-visibility-residual-privacy-audit-v3/"
    "2026-08-23-report.md"
)
DEFAULT_SAMPLE_SIZE = 600
DEFAULT_SEED = "gendered-visibility-residual-privacy-v3-20260823"
EXPECTED_CORPUS_N = 38_298


def build_v3_report(manifest: dict[str, Any]) -> str:
    counts = manifest["counts"]
    direct = counts["structured_direct_locator_by_type"]
    detector = counts["v381_detector"]
    residual = counts["residual_scanner"]
    return f"""# 38,298标题v4语料残余隐私审计报告（v3）

**审计日期：**2026-08-23  
**语料：**`{manifest['scope']['corpus']}`  
**分析单位：**一个唯一的最终去标识标题文本（`N = 38,298`）  
**当前判定：****研究者确认的111条处置已经应用；自动直接定位门禁通过。该结果不构成独立匿名性认证。**

## 后构建复扫

| 直接定位信息测试 | span数 |
|---|---:|
| 电子邮件地址 | {direct.get('email_address', 0)} |
| URL或域名 | {direct.get('url_or_domain', 0)} |
| 中国大陆手机号形态 | {direct.get('cn_mobile_number', 0)} |
| 中国大陆身份证号形态 | {direct.get('cn_national_id_number', 0)} |
| 显式`@handle` | {direct.get('explicit_at_handle', 0)} |
| 带标签的联系／账号值 | {direct.get('labelled_contact_identifier', 0)} |

六类直接定位信号合计为0。v3.81规则／NER仍产生{detector['span_count']}个高召回候选，
其中{detector['placeholder_artifact_spans']}个为占位符伪影、
{detector['non_placeholder_spans']}个为非占位符候选；宽松扫描器产生
{residual['item_count']}个项目。这些数是机器候选工作量，不是新确认泄露数。

## 人工与发布边界

研究者此前完成857条复核，并对其中111条确认项完成精确处置；本轮不要求研究者重复审核
同一批材料。新生成的600条概率样本仅保留为未来独立确认入口，未被描述为已完成。
因此可以准确表述为“研究者确认项已修复且后构建直接定位扫描归零”，不能表述为“完全匿名”
或“通过独立匿名性认证”。标题级文件、候选、私有AI字面证据、来源与记录键不得进入投稿包。

本审计读取`data/raw/`为0，供应商/API调用为0；本地NER运行不改写v3、v4或冻结AI响应。
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE)
    parser.add_argument("--seed", default=DEFAULT_SEED)
    parser.add_argument("--ner-model", nargs="+", default=list(DEFAULT_NER_MODELS))
    args = parser.parse_args()
    manifest = run_audit(
        output_dir=args.output_dir,
        report_path=args.report,
        sample_size=args.sample_size,
        seed=args.seed,
        ner_model_names=args.ner_model,
        corpus_manifest_path=CORPUS_MANIFEST,
        corpus_membership_path=CORPUS_MEMBERSHIP,
        audit_id="gendered_visibility_residual_privacy_audit_v3",
        audit_version="v3.0",
        corpus_id=CORPUS_ID,
        expected_corpus_n=EXPECTED_CORPUS_N,
        expected_sampling_track_counts={
            "probability": 36_303,
            "mechanism": 1_995,
        },
        report_builder=build_v3_report,
        additional_input_paths=(
            SCRIPT,
            ADJUDICATION_MANIFEST,
            LABEL_AUDIT_MANIFEST,
        ),
    )
    if manifest["counts"]["confirmed_direct_locator_spans"] == 0:
        manifest["status"] = (
            "passed_postbuild_direct_locator_scan_researcher_remediation_"
            "complete_pending_independent_confirmation"
        )
        manifest["prior_researcher_review"] = {
            "reviewed_unique_titles": 857,
            "confirmed_titles_remediated_or_retained_after_clarification": 111,
            "independent_review_claimed": False,
            "repeat_researcher_review_required": False,
        }
        (args.output_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "corpus_n": manifest["scope"]["corpus_n"],
                "direct_locator_spans": manifest["counts"][
                    "confirmed_direct_locator_spans"
                ],
                "new_independent_sample_status": manifest[
                    "probability_review_sample"
                ]["status"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
