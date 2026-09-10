#!/usr/bin/env python3
"""Audit residual identifiers in the frozen 38,300-title paper corpus.

The audit is deliberately recall-oriented.  It reconstructs the frozen unique
title texts from immutable annotation provenance, runs the current v3.81
de-identification detector again, runs the lighter residual scanner, and adds
an independent set of structured-identifier patterns.  Candidate strings and
titles are written only below a mode-0600 ``private`` directory.  Public-facing
outputs contain aggregate counts only.

This script audits and reports; it never changes the frozen corpus or any raw
input.  Machine candidates are not treated as confirmed identifiers except for
direct locator forms (for example a literal URL, email address, telephone
number, national identity number, or explicit social handle).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.analysis.analyze_gendered_visibility_paper_corpus_v1 import (
    Unit,
    load_units,
    resolve_title_overrides_path,
)
from src.preprocessing.detect_deidentification_candidates import (
    DEFAULT_NER_MODELS,
    RULE_VERSION,
    detect_candidates,
    load_ner_models,
)
from src.preprocessing.scan_deidentification_residuals import (
    REPLACEMENT_TERMS,
    SCAN_VERSION,
    scan_rows,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = Path(__file__).resolve()
CORPUS_DIR = (
    ROOT
    / "data/processed/gendered_visibility_paper_corpus_v2_text_deduplicated"
)
CORPUS_MANIFEST = CORPUS_DIR / "manifest.json"
CORPUS_MEMBERSHIP = CORPUS_DIR / "private/analytical_corpus_membership.csv"
DEIDENTIFIED_FRAME = (
    ROOT / "data/interim/11_deidentification/titles_for_analysis_deidentified.csv"
)
DEFAULT_OUTPUT_DIR = (
    ROOT / "data/processed/gendered_visibility_residual_privacy_audit_v1"
)
DEFAULT_REPORT = (
    ROOT
    / "results/logs/gendered-visibility-residual-privacy-audit-v1/"
    "2026-08-23-report.md"
)
AUDIT_VERSION = "v1.0"
EXPECTED_CORPUS_N = 38_300
DEFAULT_SAMPLE_SIZE = 600
DEFAULT_SEED = "gendered-visibility-residual-privacy-v1-20260823"


@dataclass(frozen=True)
class StructuredPattern:
    code: str
    risk_class: str
    pattern: re.Pattern[str]
    interpretation: str


@dataclass(frozen=True)
class StructuredFinding:
    code: str
    risk_class: str
    span_start: int
    span_end: int
    matched_text: str
    interpretation: str


STRUCTURED_PATTERNS: tuple[StructuredPattern, ...] = (
    StructuredPattern(
        "email_address",
        "direct_locator",
        re.compile(
            r"(?i)(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?!\w)"
        ),
        "Literal email-address form.",
    ),
    StructuredPattern(
        "url_or_domain",
        "direct_locator",
        re.compile(
            r"(?i)(?:https?://|www\.)\S+|(?<![\w@])(?:[a-z0-9-]+\.)+"
            r"(?:com|cn|net|org|tv|me|cc|xyz)(?:/\S*)?"
        ),
        "Literal URL or domain form that can directly locate an account or page.",
    ),
    StructuredPattern(
        "cn_mobile_number",
        "direct_locator",
        re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)"),
        "Mainland-China mobile-number form.",
    ),
    StructuredPattern(
        "cn_national_id_number",
        "direct_locator",
        re.compile(
            r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])"
            r"(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)"
        ),
        "Mainland-China national identity-number form.",
    ),
    StructuredPattern(
        "explicit_at_handle",
        "direct_locator",
        re.compile(r"(?<![\w@])@[A-Za-z0-9_][A-Za-z0-9_.-]{2,31}"),
        "Explicit at-prefixed account handle.",
    ),
    StructuredPattern(
        "labelled_contact_identifier",
        "direct_locator",
        re.compile(
            r"(?i)(?:\u5fae\u4fe1(?:\u53f7)?|wechat|vx|qq(?:\u53f7)?|telegram|tg(?:\u53f7)?|"
            r"\u7535\u62a5|\u5fae\u535a(?:\u53f7)?|\u6296\u97f3(?:\u53f7)?|\u63a8\u7279(?:\u53f7)?|\u8d26\u53f7|(?:user)?id)"
            r"\s*[:\uff1a@]\s*(?:[A-Za-z][A-Za-z0-9_.-]{2,31}|\d{5,12})"
        ),
        "Account/contact label followed by a locator value.",
    ),
    StructuredPattern(
        "long_digit_sequence",
        "review_required",
        re.compile(r"(?<!\d)\d{7,19}(?!\d)"),
        "Long digit sequence; dates and catalogue codes are common false positives.",
    ),
    StructuredPattern(
        "address_like_phrase",
        "review_required",
        re.compile(
            r"[\u4e00-\u9fff]{2,12}(?:\u8def|\u8857|\u5df7|\u9053|\u9547|\u4e61|\u6751|\u5c0f\u533a|\u516c\u5bd3)"
            r"\s*\d{1,5}(?:\u53f7|\u680b|\u5ba4|\u697c)"
        ),
        "Address-shaped phrase requiring contextual adjudication.",
    ),
)

PUBLIC_FORBIDDEN_FIELDS = {
    "matched_text",
    "context",
    "deidentified_title",
    "title_sha256",
    "record_id",
    "source_site",
    "url",
    "evidence",
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


def candidate_id(
    title_sha256: str,
    detector: str,
    signal: str,
    start: int,
    end: int,
    matched_text: str,
) -> str:
    payload = "\x1f".join(
        (title_sha256, detector, signal, str(start), str(end), matched_text)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def stable_rank(seed: str, label: str, title_sha256: str) -> str:
    return hashlib.sha256(
        f"{seed}\x1f{label}\x1f{title_sha256}".encode()
    ).hexdigest()


def stable_sample(
    units: Sequence[Unit], sample_size: int, seed: str
) -> list[Unit]:
    if sample_size <= 0 or sample_size > len(units):
        raise ValueError("sample_size must be between 1 and the corpus size")
    return sorted(
        units,
        key=lambda unit: stable_rank(seed, "probability_review", unit.title_sha256),
    )[:sample_size]


def structured_findings(title: str) -> list[StructuredFinding]:
    findings: list[StructuredFinding] = []
    seen: set[tuple[str, int, int]] = set()
    for spec in STRUCTURED_PATTERNS:
        for match in spec.pattern.finditer(title):
            key = (spec.code, match.start(), match.end())
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                StructuredFinding(
                    code=spec.code,
                    risk_class=spec.risk_class,
                    span_start=match.start(),
                    span_end=match.end(),
                    matched_text=match.group(0),
                    interpretation=spec.interpretation,
                )
            )
    return findings


def placeholder_spans(title: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for term in sorted(REPLACEMENT_TERMS, key=len, reverse=True):
        start = 0
        while True:
            index = title.find(term, start)
            if index < 0:
                break
            spans.append((index, index + len(term)))
            start = index + 1
    return spans


def is_placeholder_artifact(
    title: str, start: int, end: int, matched_text: str
) -> bool:
    """Flag re-scan hits caused by or inseparable from an existing placeholder."""

    if "\u67d0" in matched_text or matched_text in REPLACEMENT_TERMS:
        return True
    return any(start < placeholder_end and end > placeholder_start for placeholder_start, placeholder_end in placeholder_spans(title))


def context_window(title: str, start: int, end: int, width: int = 36) -> str:
    return title[max(0, start - width) : min(len(title), end + width)]


def one_sided_zero_upper(sample_size: int, confidence: float = 0.95) -> float:
    """Exact binomial upper bound for zero observed events (if review is done)."""

    if sample_size <= 0 or not 0 < confidence < 1:
        raise ValueError("invalid sample size or confidence")
    return 1 - (1 - confidence) ** (1 / sample_size)


def write_csv_private(path: Path, fieldnames: Sequence[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fieldnames, extrasaction="raise", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)
    os.chmod(path, 0o600)


def write_csv_public(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0]) if rows else ["status"]
    if PUBLIC_FORBIDDEN_FIELDS & set(fieldnames):
        raise RuntimeError("public aggregate output contains a private field")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fieldnames, extrasaction="raise", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def load_targeted_adjudication(
    path: Path, candidate_ids: set[str]
) -> dict[str, Any]:
    if not path.exists():
        return {
            "status": "not_performed",
            "reviewed_items": 0,
            "by_decision": {},
        }
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    required = {"candidate_id", "review_decision", "review_note"}
    if not rows or set(rows[0]) != required:
        raise RuntimeError("targeted adjudication schema is invalid")
    row_ids = [row["candidate_id"] for row in rows]
    if len(row_ids) != len(set(row_ids)):
        raise RuntimeError("duplicate candidate ID in targeted adjudication")
    if not set(row_ids).issubset(candidate_ids):
        raise RuntimeError("targeted adjudication references an unknown candidate")
    allowed = {"confirmed_direct_locator", "false_positive", "unclear"}
    if any(row["review_decision"] not in allowed for row in rows):
        raise RuntimeError("invalid targeted adjudication decision")
    if any(not row["review_note"].strip() for row in rows):
        raise RuntimeError("targeted adjudication note is required")
    return {
        "status": "completed_targeted_not_probability_review",
        "reviewed_items": len(rows),
        "by_decision": dict(
            sorted(Counter(row["review_decision"] for row in rows).items())
        ),
        "adjudication_path": path.relative_to(ROOT).as_posix(),
        "adjudication_sha256": sha256_file(path),
        "interpretation": (
            "Targeted local content review covers structured-pattern hits and "
            "high-priority CJK residual-scanner hits. It is not an independent "
            "probability-sample review and cannot estimate prevalence."
        ),
    }


def validate_units(
    units: Sequence[Unit],
    *,
    expected_corpus_n: int = EXPECTED_CORPUS_N,
    expected_sampling_track_counts: dict[str, int] | None = None,
) -> None:
    if expected_sampling_track_counts is None:
        expected_sampling_track_counts = {
            "probability": 36_305,
            "mechanism": 1_995,
        }
    if len(units) != expected_corpus_n:
        raise RuntimeError("paper-corpus count drifted")
    if len({unit.title_sha256 for unit in units}) != expected_corpus_n:
        raise RuntimeError("paper corpus is not unique by final title text")
    if Counter(unit.sampling_track for unit in units) != expected_sampling_track_counts:
        raise RuntimeError("sampling-track counts drifted")
    for unit in units:
        observed = hashlib.sha256(unit.title.encode("utf-8")).hexdigest()
        if observed != unit.title_sha256:
            raise RuntimeError("reconstructed title does not match frozen hash")


def _detector_rows(units: Sequence[Unit]) -> list[dict[str, str]]:
    return [
        {
            "record_id": unit.title_sha256,
            "source_site": "",
            "data_round": "",
            "cleaned_title": unit.title,
            "deidentified_title": unit.title,
        }
        for unit in units
    ]


def _candidate_private_row(
    unit: Unit,
    *,
    detector: str,
    signal: str,
    priority: str,
    start: int,
    end: int,
    matched_text: str,
    automated_disposition: str,
) -> dict[str, Any]:
    return {
        "candidate_id": candidate_id(
            unit.title_sha256, detector, signal, start, end, matched_text
        ),
        "corpus_index": unit.corpus_index,
        "title_sha256": unit.title_sha256,
        "sampling_track": unit.sampling_track,
        "detector": detector,
        "signal": signal,
        "priority": priority,
        "span_start": start,
        "span_end": end,
        "matched_text": matched_text,
        "context": context_window(unit.title, start, end),
        "deidentified_title": unit.title,
        "placeholder_artifact": str(
            is_placeholder_artifact(unit.title, start, end, matched_text)
        ).lower(),
        "automated_disposition": automated_disposition,
        "review_decision": "",
        "review_note": "",
    }


def build_report(manifest: dict[str, Any]) -> str:
    counts = manifest["counts"]
    direct = counts["structured_direct_locator_by_type"]
    structured = counts["structured_by_type"]
    detector = counts["v381_detector"]
    residual = counts["residual_scanner"]
    sample = manifest["probability_review_sample"]
    targeted = manifest["targeted_local_review"]
    targeted_counts = targeted["by_decision"]
    if targeted["status"] == "completed_targeted_not_probability_review":
        targeted_section = f"""在不把敏感字符串写入报告的前提下，本轮对
{targeted['reviewed_items']}个定向项目作了本地内容复核：
{targeted_counts.get('confirmed_direct_locator', 0)}个确认直接定位信息，
{targeted_counts.get('false_positive', 0)}个误报，以及
{targeted_counts.get('unclear', 0)}个仍需与拉丁字符handle合并判断的数字片段。22个
高优先级中文姓名形态／高风险语境命中均为泛化描述、地名、虚构／创作
引用或商品词，不是已验证的现实定位信息。误报还包括明确日期、番号以及
一个创作性场景名。逐项候选ID和决定仅保存于私有文件。

这一定向复核有助于候选分流，但它不是独立盲审，不能据此估计总体残余率。"""
    else:
        targeted_section = (
            "未提供定向本地复核文件；所有结构化与语言学候选因此仍只是机器命中。"
        )
    return f"""# 38,300标题语料残余隐私审计报告（v1）

**审计日期：**2026-08-23  
**语料：**`gendered_visibility_paper_corpus_v2_text_deduplicated`  
**分析单位：**一个唯一的最终去标识标题文本（`N = 38,300`）  
**审计判定：****未通过。在声称“完全去标识”或以任何形式共享标题级文本前，
必须先修复并重新审计。**

## 一、核心结论

冻结语料通过了成员关系、唯一性、标题哈希和抽样轨道完整性检查。但全语料
结构化扫描仍在**{counts['confirmed_direct_locator_titles']}个标题中确认了
{counts['confirmed_direct_locator_spans']}个可直接定位账号／页面的链接**。本报告不重现链接、
标题、哈希、来源或账号字符串；详情仅保存在权限0600的私有候选文件中。

| 直接定位信息测试 | span数 |
|---|---:|
| 电子邮件地址 | {direct.get('email_address', 0)} |
| URL或域名 | {direct.get('url_or_domain', 0)} |
| 中国大陆手机号形态 | {direct.get('cn_mobile_number', 0)} |
| 中国大陆身份证号形态 | {direct.get('cn_national_id_number', 0)} |
| 显式`@handle` | {direct.get('explicit_at_handle', 0)} |
| 带标签的联系／账号值 | {direct.get('labelled_contact_identifier', 0)} |

只要存在这一个确认项，“零残余”隐私门禁就必须判定失败。这不表示实际
残余标识只有{counts['confirmed_direct_locator_titles']}条；长尾候选仍未完成独立复核。

## 二、全语料偏召回扫描

本轮对38,300个唯一文本使用了三层方法：

1. 在已去标识文本上重跑当前v3.81规则和两个本地中文NER模型，命中
   {detector['title_count']}个标题、{detector['span_count']}个span。其中
   {detector['placeholder_artifact_spans']}个与既有“某人／某机构”类占位符重叠，属于替换后
   产生的检测伪影；剩余{detector['non_placeholder_spans']}个需复核。
2. 使用更宽松的残余扫描器，命中{residual['title_count']}个标题、
   {residual['item_count']}个项目：{residual['high_priority_items']}个高优先级中文姓名／语境候选和
   {residual['medium_priority_items']}个中优先级拉丁字符候选（
   {residual['unique_latin_candidate_strings']}个唯一字符串）。已注册别名和已知机构词表的字面残留为0。
3. 独立结构模式共命中{counts['structured_total_titles']}个标题、
   {counts['structured_total_spans']}个span。除已确认链接外，包括
   {structured.get('long_digit_sequence', 0)}个长数字候选和
   {structured.get('address_like_phrase', 0)}个地址形态候选。

三层方法的候选并集覆盖{counts['all_candidate_union_titles']}个标题。这些都是为提高召回率而产生的
复核队列，**不能直接当作隐私泄露数或泄露率**。两个语言学扫描器也不是彼此
独立的金标准。

## 三、定向本地复核

{targeted_section}

## 四、概率抽样复核与当前限制

已从全38,300个唯一文本中，以固定种子生成{sample['sample_size']}条简单概率复核队列，
并以权限0600保存。本轮**没有**把该队列冒充为已完成的独立人工复核。只有当一名
真正独立的复核者审完全部{sample['sample_size']}条且确认0个残余后，才可报告“单侧95%
精确二项分布上界为{sample['zero_event_upper_95']:.4%}”；当前不得使用该上界。

因此，本审计能够严谨支持的结论只是：**至少还有1个直接定位信息，且拉丁姓名／
handle长尾队列未决。**本轮不能估计完整残余率，也不能证明100%匿名。

## 五、对投稿、伦理和数据治理的影响

- 不应发布、作为附件提交或向数据仓存入标题级文本，即使它被称为“已去标识”。
  当前“只公开结构化汇总”的政策必须保留。
- 已确认链接所属标题是当前AI编码语料成员；因此除了本地修复，还应按机构政策追溯
  既往外部AI请求、供应商留存／删除规则和事件记录要求。
- 本技术审计不代替伦理审查、平台条款评估、合法处理基础判定或数据保护负责人
  的意见。`Ethics Approval`仍需一个明确的机构结论：获批、获豁免，或经审查后确认
  不需要，并附理由。
- 论文可使用边界明确的表述：“研究使用规则与NER辅助遮蔽直接标识；标题级
  文本只保存于受控研究环境，公开输出仅含汇总。”不应写“完全匿名”或“无残余”。

## 六、修复顺序

1. 保留冻结v2语料不变；在新的`data/interim/`或`data/processed/`层中隔离或重新遮蔽
   确认链接，并扩展URL和复合拉丁handle规则。
2. 完成私有600条概率样本与拉丁字符定向队列的独立复核；报告复核者人数、操作定义、
   确认数和不确定性上界。现有两名编码者的400标题复核只用于AI编码准确性，不得改称为
   隐私审计。
3. 修复后重建最终文本哈希、语料成员和所有依赖该哈希的下游结果，然后重跑本审计。
   不覆盖旧输出。
4. 根据机构意见处理既往外部AI传输的记录、留存和报告义务，并在论文中如实披露
   去标识方法与限制。

## 七、复现与隐私控制

- 读取`data/raw/`：0个文件。
- 供应商／API调用：0；两个NER模型均为本地运行。
- 改写冻结源输出：0。
- 敏感候选和复核标题均位于`private/`，权限为0600。
- 本报告与聚合CSV不含标题、来源、实际URL、记录ID、标题哈希、候选字符串或证据span。
- 输入、参数、计数、软件版本、输出路径和SHA-256记录于`manifest.json`和
  `docs/research_log.md`。
"""


def run_audit(
    *,
    output_dir: Path,
    report_path: Path,
    sample_size: int,
    seed: str,
    ner_model_names: Sequence[str],
    corpus_manifest_path: Path = CORPUS_MANIFEST,
    corpus_membership_path: Path = CORPUS_MEMBERSHIP,
    audit_id: str = "gendered_visibility_residual_privacy_audit_v1",
    audit_version: str = AUDIT_VERSION,
    corpus_id: str = "gendered_visibility_paper_corpus_v2_text_deduplicated",
    expected_corpus_n: int = EXPECTED_CORPUS_N,
    expected_sampling_track_counts: dict[str, int] | None = None,
    report_builder: Any = build_report,
    additional_input_paths: Sequence[Path] = (),
) -> dict[str, Any]:
    units, _ = load_units(
        freeze_manifest_path=corpus_manifest_path,
        membership_path=corpus_membership_path,
        expected_corpus_n=expected_corpus_n,
        require_exact_valid_output_membership=False,
    )
    validate_units(
        units,
        expected_corpus_n=expected_corpus_n,
        expected_sampling_track_counts=expected_sampling_track_counts,
    )
    unit_by_hash = {unit.title_sha256: unit for unit in units}
    detector_rows = _detector_rows(units)

    models = load_ner_models(ner_model_names)
    v381_candidates = detect_candidates(
        detector_rows, nlp_models=models, text_layers=("cleaned_title",)
    )
    residual_items = scan_rows(detector_rows)

    private_rows: list[dict[str, Any]] = []
    for candidate in v381_candidates:
        unit = unit_by_hash[candidate.record_id]
        artifact = is_placeholder_artifact(
            unit.title,
            candidate.span_start,
            candidate.span_end,
            candidate.matched_text,
        )
        private_rows.append(
            _candidate_private_row(
                unit,
                detector="v381_rule_ner_rescan",
                signal=candidate.signal_type,
                priority=("low" if artifact else candidate.confidence),
                start=candidate.span_start,
                end=candidate.span_end,
                matched_text=candidate.matched_text,
                automated_disposition=(
                    "placeholder_artifact" if artifact else "review_required"
                ),
            )
        )

    for item in residual_items:
        unit = unit_by_hash[item["record_id"]]
        start = int(item["span_start"])
        end = int(item["span_end"])
        private_rows.append(
            _candidate_private_row(
                unit,
                detector="residual_scanner",
                signal=item["scan_type"],
                priority=item["priority"],
                start=start,
                end=end,
                matched_text=item["matched_text"],
                automated_disposition="review_required",
            )
        )

    structured_counter: Counter[str] = Counter()
    structured_title_sets: defaultdict[str, set[str]] = defaultdict(set)
    direct_counter: Counter[str] = Counter()
    direct_title_hashes: set[str] = set()
    for unit in units:
        for finding in structured_findings(unit.title):
            structured_counter[finding.code] += 1
            structured_title_sets[finding.code].add(unit.title_sha256)
            if finding.risk_class == "direct_locator":
                direct_counter[finding.code] += 1
                direct_title_hashes.add(unit.title_sha256)
            private_rows.append(
                _candidate_private_row(
                    unit,
                    detector="structured_pattern",
                    signal=finding.code,
                    priority=(
                        "confirmed_direct_locator"
                        if finding.risk_class == "direct_locator"
                        else "medium"
                    ),
                    start=finding.span_start,
                    end=finding.span_end,
                    matched_text=finding.matched_text,
                    automated_disposition=(
                        "confirmed_direct_locator"
                        if finding.risk_class == "direct_locator"
                        else "review_required"
                    ),
                )
            )

    private_rows.sort(
        key=lambda row: (
            row["automated_disposition"] != "confirmed_direct_locator",
            row["priority"] not in {"high", "strong"},
            int(row["corpus_index"]),
            row["detector"],
            int(row["span_start"]),
        )
    )
    private_dir = output_dir / "private"
    candidates_path = private_dir / "candidate_items.csv"
    candidate_fieldnames = [
        "candidate_id",
        "corpus_index",
        "title_sha256",
        "sampling_track",
        "detector",
        "signal",
        "priority",
        "span_start",
        "span_end",
        "matched_text",
        "context",
        "deidentified_title",
        "placeholder_artifact",
        "automated_disposition",
        "review_decision",
        "review_note",
    ]
    write_csv_private(candidates_path, candidate_fieldnames, private_rows)
    adjudication_path = private_dir / "targeted_local_adjudication.csv"
    targeted_review = load_targeted_adjudication(
        adjudication_path, {row["candidate_id"] for row in private_rows}
    )
    if adjudication_path.exists():
        os.chmod(adjudication_path, 0o600)

    probability_sample = stable_sample(units, sample_size, seed)
    sample_path = private_dir / "probability_review_sample.csv"
    write_csv_private(
        sample_path,
        (
            "review_index",
            "corpus_index",
            "title_sha256",
            "sampling_track",
            "deidentified_title",
            "review_decision",
            "review_note",
        ),
        (
            {
                "review_index": index,
                "corpus_index": unit.corpus_index,
                "title_sha256": unit.title_sha256,
                "sampling_track": unit.sampling_track,
                "deidentified_title": unit.title,
                "review_decision": "",
                "review_note": "",
            }
            for index, unit in enumerate(probability_sample, start=1)
        ),
    )

    latin_queue_path = private_dir / "latin_handle_review_queue.csv"
    latin_items = [
        item
        for item in residual_items
        if item["scan_type"] == "latin_handle_suspicion"
    ]
    write_csv_private(
        latin_queue_path,
        (
            "review_index",
            "candidate_id",
            "corpus_index",
            "title_sha256",
            "sampling_track",
            "matched_text",
            "context",
            "deidentified_title",
            "review_decision",
            "review_note",
        ),
        (
            {
                "review_index": index,
                "candidate_id": candidate_id(
                    item["record_id"],
                    "residual_scanner",
                    item["scan_type"],
                    int(item["span_start"]),
                    int(item["span_end"]),
                    item["matched_text"],
                ),
                "corpus_index": unit_by_hash[item["record_id"]].corpus_index,
                "title_sha256": item["record_id"],
                "sampling_track": unit_by_hash[item["record_id"]].sampling_track,
                "matched_text": item["matched_text"],
                "context": context_window(
                    unit_by_hash[item["record_id"]].title,
                    int(item["span_start"]),
                    int(item["span_end"]),
                ),
                "deidentified_title": unit_by_hash[item["record_id"]].title,
                "review_decision": "",
                "review_note": "",
            }
            for index, item in enumerate(latin_items, start=1)
        ),
    )

    review_protocol_path = private_dir / "README.md"
    review_protocol_path.write_text(
        """# 独立残余隐私复核包

请由未参与本次自动判定的复核者完成两个文件：

1. `probability_review_sample.csv`：逐条判断是否仍含可识别或可直接定位的现实个人、账号、联系值、精确地址或其他组合后足以定位个人的信息。
2. `latin_handle_review_queue.csv`：重点判断拉丁字符片段是普通词、片名／人物名、技术词、虚构名，还是现实姓名、用户名或平台账号。

`review_decision`只填`confirmed_identifier`、`false_positive`或`unclear`；每个非误报项目都必须在`review_note`说明理由。复核者不得进行反向搜索、点击链接或联系相关个人。600条概率样本必须全部完成后才能计算零事件上界；拉丁队列用于关闭机器扫描长尾，不能代替概率样本。
""",
        encoding="utf-8",
    )
    os.chmod(review_protocol_path, 0o600)

    v381_title_hashes = {candidate.record_id for candidate in v381_candidates}
    placeholder_artifacts = [
        candidate
        for candidate in v381_candidates
        if is_placeholder_artifact(
            unit_by_hash[candidate.record_id].title,
            candidate.span_start,
            candidate.span_end,
            candidate.matched_text,
        )
    ]
    residual_title_hashes = {item["record_id"] for item in residual_items}
    known_literal_signals = {
        "registered_alias_literal",
        "spaced_registered_alias",
        "known_org_phrase_literal",
    }
    known_literal_items = sum(
        item["scan_type"] in known_literal_signals for item in residual_items
    )
    structured_all_titles = set().union(*structured_title_sets.values()) if structured_title_sets else set()
    track_counts = Counter(unit.sampling_track for unit in units)

    aggregate_rows = [
        {
            "section": "corpus",
            "metric": "unique_final_deidentified_title_texts",
            "count": len(units),
            "interpretation": "frozen audit population",
        },
        *(
            {
                "section": "structured_pattern",
                "metric": code,
                "count": count,
                "interpretation": next(
                    spec.interpretation for spec in STRUCTURED_PATTERNS if spec.code == code
                ),
            }
            for code, count in sorted(structured_counter.items())
        ),
        {
            "section": "v381_rule_ner_rescan",
            "metric": "candidate_spans",
            "count": len(v381_candidates),
            "interpretation": "high-recall candidates, not confirmed identifiers",
        },
        {
            "section": "residual_scanner",
            "metric": "candidate_items",
            "count": len(residual_items),
            "interpretation": "high-recall candidates, not confirmed identifiers",
        },
    ]
    aggregate_path = output_dir / "aggregate_results.csv"
    write_csv_public(aggregate_path, aggregate_rows)

    manifest: dict[str, Any] = {
        "created_at": utc_now(),
        "audit_id": audit_id,
        "audit_version": audit_version,
        "status": (
            "failed_confirmed_direct_locator_and_unresolved_candidates"
            if direct_counter
            else "pending_independent_residual_candidate_review"
        ),
        "scope": {
            "corpus": corpus_id,
            "unit": "unique final de-identified title text",
            "corpus_n": len(units),
            "sampling_tracks": dict(sorted(track_counts.items())),
            "raw_data_files_read": 0,
            "provider_api_calls": 0,
            "frozen_source_outputs_rewritten": 0,
        },
        "parameters": {
            "audit_seed": seed,
            "probability_review_sample_size": sample_size,
            "deidentification_detector_rule_version": RULE_VERSION,
            "residual_scanner_version": SCAN_VERSION,
            "ner_models": list(ner_model_names),
            "structured_pattern_codes": [spec.code for spec in STRUCTURED_PATTERNS],
        },
        "counts": {
            "confirmed_direct_locator_spans": sum(direct_counter.values()),
            "confirmed_direct_locator_titles": len(direct_title_hashes),
            "structured_direct_locator_by_type": {
                spec.code: direct_counter.get(spec.code, 0)
                for spec in STRUCTURED_PATTERNS
                if spec.risk_class == "direct_locator"
            },
            "structured_total_spans": sum(structured_counter.values()),
            "structured_total_titles": len(structured_all_titles),
            "structured_by_type": {
                spec.code: structured_counter.get(spec.code, 0)
                for spec in STRUCTURED_PATTERNS
            },
            "structured_titles_by_type": {
                spec.code: len(structured_title_sets.get(spec.code, set()))
                for spec in STRUCTURED_PATTERNS
            },
            "v381_detector": {
                "span_count": len(v381_candidates),
                "title_count": len(v381_title_hashes),
                "placeholder_artifact_spans": len(placeholder_artifacts),
                "non_placeholder_spans": len(v381_candidates) - len(placeholder_artifacts),
                "by_signal": dict(
                    sorted(
                        Counter(
                            signal
                            for candidate in v381_candidates
                            for signal in candidate.signal_type.split("|")
                        ).items()
                    )
                ),
            },
            "residual_scanner": {
                "item_count": len(residual_items),
                "title_count": len(residual_title_hashes),
                "high_priority_items": sum(
                    item["priority"] == "high" for item in residual_items
                ),
                "medium_priority_items": sum(
                    item["priority"] == "medium" for item in residual_items
                ),
                "known_alias_or_org_literal_items": known_literal_items,
                "unique_latin_candidate_strings": len(
                    {
                        item["matched_text"]
                        for item in residual_items
                        if item["scan_type"] == "latin_handle_suspicion"
                    }
                ),
                "by_signal": dict(
                    sorted(Counter(item["scan_type"] for item in residual_items).items())
                ),
            },
            "all_private_candidate_rows": len(private_rows),
            "all_candidate_union_titles": len(
                v381_title_hashes | residual_title_hashes | structured_all_titles
            ),
        },
        "probability_review_sample": {
            "status": "generated_not_adjudicated",
            "sample_size": sample_size,
            "seed": seed,
            "zero_event_upper_95": one_sided_zero_upper(sample_size),
            "interpretation": (
                "The upper bound is hypothetical and must not be reported unless "
                "an independent reviewer adjudicates the full queue with zero events."
            ),
        },
        "targeted_local_review": targeted_review,
        "privacy_controls": {
            "public_outputs_are_aggregate_only": True,
            "private_candidate_file_mode": oct(candidates_path.stat().st_mode & 0o777),
            "private_sample_file_mode": oct(sample_path.stat().st_mode & 0o777),
            "private_latin_queue_file_mode": oct(
                latin_queue_path.stat().st_mode & 0o777
            ),
            "public_forbidden_fields": sorted(PUBLIC_FORBIDDEN_FIELDS),
        },
        "inputs": {
            path.relative_to(ROOT).as_posix(): sha256_file(path)
            for path in (
                corpus_manifest_path,
                corpus_membership_path,
                DEIDENTIFIED_FRAME,
                SCRIPT,
                *additional_input_paths,
            )
        },
        "outputs": {},
    }

    report_path.parent.mkdir(parents=True, exist_ok=True)
    override_path = resolve_title_overrides_path(
        json.loads(corpus_manifest_path.read_text(encoding="utf-8")),
        corpus_manifest_path,
    )
    if override_path is not None:
        manifest["inputs"][override_path.relative_to(ROOT).as_posix()] = sha256_file(
            override_path
        )
    report_path.write_text(report_builder(manifest), encoding="utf-8")
    manifest_path = output_dir / "manifest.json"
    manifest["outputs"] = {
        path.relative_to(ROOT).as_posix(): sha256_file(path)
        for path in (
            aggregate_path,
            candidates_path,
            sample_path,
            latin_queue_path,
            review_protocol_path,
            report_path,
            *([adjudication_path] if adjudication_path.exists() else []),
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
    parser.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE)
    parser.add_argument("--seed", default=DEFAULT_SEED)
    parser.add_argument(
        "--ner-model", nargs="+", default=list(DEFAULT_NER_MODELS)
    )
    args = parser.parse_args()
    manifest = run_audit(
        output_dir=args.output_dir,
        report_path=args.report,
        sample_size=args.sample_size,
        seed=args.seed,
        ner_model_names=args.ner_model,
    )
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "corpus_n": manifest["scope"]["corpus_n"],
                "confirmed_direct_locator_spans": manifest["counts"][
                    "confirmed_direct_locator_spans"
                ],
                "private_candidate_rows": manifest["counts"][
                    "all_private_candidate_rows"
                ],
                "output_dir": args.output_dir.relative_to(ROOT).as_posix(),
                "report": args.report.relative_to(ROOT).as_posix(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
