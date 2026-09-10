"""Rule 015: remove confirmed organizer-added prefixes from analysis titles.

The 197 recurring strings recorded by the retracted rule 003 were initially
treated as possible series names. The researcher later confirmed that they
are organizer-added grouping labels and that every retained row represents
an independent video. This rule removes that label and an immediately
following numeric organizer identifier from the text used for analysis.

No record is excluded, merged, or assigned to a series. The pre-rule title,
historical label, actually removed label, and removed number remain in every
output row so the transformation is reversible and auditable.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import re
from pathlib import Path

try:
    from .apply_brand_code_rule import strip_brand_and_code
    from .apply_freetext_marketing_cleanup import clean_freetext
    from .empty_title_fallback import apply_empty_title_fallback
except ImportError:  # Direct script execution.
    from apply_brand_code_rule import strip_brand_and_code
    from apply_freetext_marketing_cleanup import clean_freetext
    from empty_title_fallback import apply_empty_title_fallback


ROOT = Path(__file__).resolve().parents[2]
ANALYSIS_INPUT = ROOT / "data" / "interim" / "04_deduplicated" / "titles_for_analysis.csv"
AUDIT_INPUT = ROOT / "data" / "interim" / "04_deduplicated" / "titles_deduplicated.jsonl"
OUTPUT_DIR = ROOT / "data" / "interim" / "04_organizer_prefix_cleaned"
JSONL_OUTPUT = OUTPUT_DIR / "titles_organizer_prefix_cleaned.jsonl"
CSV_OUTPUT = OUTPUT_DIR / "titles_for_analysis.csv"
REFERENCE_OUTPUT = OUTPUT_DIR / "organizer_prefix_reference.txt"

RULE_ID = "organizer_prefix_noise_removal"
RULE_VERSION = "v1.0-2026-08-01"
ORGANIZER_NUMBER_RE = re.compile(
    r"^\s*(\d{1,4}(?:[-—至到]\d{1,4})?)(?=\s|$)\s*"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def line_count(path: Path) -> int:
    with path.open(encoding="utf-8", errors="replace") as handle:
        return sum(1 for _ in handle)


def visible_prefix(historical_prefix: str) -> str:
    """Apply relevant upstream rules to recover the prefix still visible now."""
    without_brand, _ = strip_brand_and_code(historical_prefix)
    return clean_freetext(without_brand).strip()


def remove_prefix_noise(title: str, historical_prefix: str) -> dict[str, str | bool]:
    if not historical_prefix:
        return {
            "cleaned_title": title,
            "organizer_prefix_removed": "",
            "organizer_number_removed": "",
            "organizer_prefix_rule_applied": False,
        }

    prefix = visible_prefix(historical_prefix)
    if not prefix or not title.startswith(prefix):
        raise ValueError(
            f"historical organizer prefix does not match current title: "
            f"prefix={historical_prefix!r}, visible={prefix!r}, title={title!r}"
        )
    if len(title) > len(prefix) and not title[len(prefix)].isspace():
        raise ValueError(
            f"organizer prefix is only a partial first token: {prefix!r} in {title!r}"
        )

    remainder = title[len(prefix) :].lstrip()
    number_match = ORGANIZER_NUMBER_RE.match(remainder)
    number = number_match.group(1) if number_match else ""
    if number_match:
        remainder = remainder[number_match.end() :]

    return {
        "cleaned_title": remainder.strip(),
        "organizer_prefix_removed": prefix,
        "organizer_number_removed": number,
        "organizer_prefix_rule_applied": True,
    }


def main() -> int:
    with ANALYSIS_INPUT.open(encoding="utf-8-sig", newline="") as handle:
        analysis_rows = list(csv.DictReader(handle))
    analysis_ids = {row["record_id"] for row in analysis_rows}
    if len(analysis_ids) != len(analysis_rows):
        raise ValueError("analysis input contains duplicate record_id values")

    audit_by_id: dict[str, dict] = {}
    with AUDIT_INPUT.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                if row["record_id"] in analysis_ids:
                    audit_by_id[row["record_id"]] = row
    if set(audit_by_id) != analysis_ids:
        raise ValueError("analysis and audit inputs do not contain the same retained record_ids")

    output_rows = []
    original_prefixes: set[str] = set()
    prefix_hits = number_hits = newly_empty = 0
    empty_title_fallback_hits = 0
    site_hits: dict[str, int] = {}
    for row in analysis_rows:
        audit = audit_by_id[row["record_id"]]
        historical_prefix = audit.get("matched_series_name_pattern_v1_retracted") or ""
        result = remove_prefix_noise(row["cleaned_title"], historical_prefix)
        if result["organizer_prefix_rule_applied"]:
            prefix_hits += 1
            original_prefixes.add(historical_prefix)
            site_hits[row["source_site"]] = site_hits.get(row["source_site"], 0) + 1
        if result["organizer_number_removed"]:
            number_hits += 1
        if row["cleaned_title"].strip() and not str(result["cleaned_title"]).strip():
            newly_empty += 1

        # Empty-title safety net (rule 018) -- this is the true last step of
        # round1's own cleaning chain (every earlier rule, including this
        # one's own organizer-prefix strip above, has already run), so this
        # is where a fully/near-fully emptied cleaned_title gets rescued
        # rather than left to reach the analysis dataset as noise. See
        # src/preprocessing/empty_title_fallback.py for the mechanism.
        fallback = apply_empty_title_fallback(row["original_title"], str(result["cleaned_title"]))
        if fallback["empty_title_fallback_applied"]:
            empty_title_fallback_hits += 1

        output_rows.append(
            {
                **row,
                "cleaned_title_before_organizer_prefix_rule": row["cleaned_title"],
                "cleaned_title": fallback["cleaned_title"],
                "historical_prefix_label_v1_retracted": historical_prefix,
                "organizer_prefix_removed": result["organizer_prefix_removed"],
                "organizer_number_removed": result["organizer_number_removed"],
                "organizer_prefix_rule_applied": result["organizer_prefix_rule_applied"],
                "organizer_prefix_rule_id": RULE_ID,
                "organizer_prefix_rule_version": RULE_VERSION,
                "cleaned_title_before_empty_title_fallback": fallback[
                    "cleaned_title_before_empty_title_fallback"
                ],
                "empty_title_fallback_applied": fallback["empty_title_fallback_applied"],
                "empty_title_fallback_source": fallback["empty_title_fallback_source"],
                "independent_video_record": True,
            }
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with JSONL_OUTPUT.open("w", encoding="utf-8") as handle:
        for row in output_rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    csv_fields = list(output_rows[0])
    with CSV_OUTPUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=csv_fields)
        writer.writeheader()
        writer.writerows(output_rows)

    REFERENCE_OUTPUT.write_text(
        "\n".join(sorted(original_prefixes)) + "\n", encoding="utf-8"
    )

    report = [
        "# 规则015：整理者前缀噪音清洗",
        "",
        "> 研究者已确认：历史上误作系列名的结构是整理者添加的分组前缀，",
        "> 每一条保留记录均代表独立视频。该字段不能作为影片系列或人物关系证据。",
        "",
        f"- 输入/输出记录：{len(output_rows):,} / {len(output_rows):,}",
        f"- 删除整理前缀：{prefix_hits:,}",
        f"- 删除紧随编号：{number_hits:,}",
        f"- 历史前缀种类：{len(original_prefixes):,}",
        f"- 清洗后新增空标题：{newly_empty:,}（记录保留）",
        f"- 空标题兜底规则（empty_title_fallback）触发次数：{empty_title_fallback_hits:,}"
        "（round1 链条末端，见 src/preprocessing/empty_title_fallback.py）",
        "- 删除或合并记录：0",
        "",
        "## 站点命中",
        "",
    ]
    report.extend(f"- `{site}`：{count:,}" for site, count in sorted(site_hits.items()))
    report.extend(
        [
            "",
            "## 规则边界",
            "",
            "- 只删除历史规则003已记录的开头标签，不从正文猜测新标签。",
            "- 只删除标签后立即出现的阿拉伯数字或数字范围。",
            "- `原创`、`视频帖`等后续结构暂不由本规则删除。",
            "- 清洗前文本、历史标签、实际删除标签与编号逐条保留。",
            "",
        ]
    )
    report_path = OUTPUT_DIR / "report.md"
    report_path.write_text("\n".join(report), encoding="utf-8")

    outputs = [JSONL_OUTPUT, CSV_OUTPUT, REFERENCE_OUTPUT, report_path]
    manifest = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "generated_by": "src/preprocessing/remove_organizer_prefix_noise.py",
        "rule_id": RULE_ID,
        "rule_version": RULE_VERSION,
        "decision": "confirmed_organizer_noise_each_record_independent_video",
        "inputs": [
            {"path": str(ANALYSIS_INPUT.relative_to(ROOT)), "sha256": sha256(ANALYSIS_INPUT)},
            {"path": str(AUDIT_INPUT.relative_to(ROOT)), "sha256": sha256(AUDIT_INPUT)},
        ],
        "parameters": {
            "historical_field": "matched_series_name_pattern_v1_retracted",
            "remove_immediate_arabic_number": True,
            "exclude_records": False,
            "merge_records": False,
        },
        "counts": {
            "records": len(output_rows),
            "prefix_removed": prefix_hits,
            "immediate_number_removed": number_hits,
            "historical_prefix_types": len(original_prefixes),
            "newly_empty_but_retained": newly_empty,
            "empty_title_fallback_applied": empty_title_fallback_hits,
        },
        "script_sha256": sha256(Path(__file__)),
        "implementation_dependencies": [
            {
                "path": "src/preprocessing/apply_brand_code_rule.py",
                "sha256": sha256(ROOT / "src" / "preprocessing" / "apply_brand_code_rule.py"),
            },
            {
                "path": "src/preprocessing/apply_freetext_marketing_cleanup.py",
                "sha256": sha256(
                    ROOT / "src" / "preprocessing" / "apply_freetext_marketing_cleanup.py"
                ),
            },
            {
                "path": "src/preprocessing/empty_title_fallback.py",
                "sha256": sha256(ROOT / "src" / "preprocessing" / "empty_title_fallback.py"),
            },
        ],
        "outputs": [
            {
                "output_path": str(path.relative_to(ROOT)),
                "output_sha256": sha256(path),
                "output_bytes": path.stat().st_size,
                "output_line_count": line_count(path),
            }
            for path in outputs
        ],
    }
    manifest_path = OUTPUT_DIR / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest["counts"], ensure_ascii=False))
    print(f"output={CSV_OUTPUT.relative_to(ROOT)} sha256={sha256(CSV_OUTPUT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
