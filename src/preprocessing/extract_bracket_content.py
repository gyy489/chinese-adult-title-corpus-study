"""Extract bracketed substrings from data/raw/titles.jsonl for manual review.

Read-only: verifies the raw snapshot against manifest.json, then pulls out the
text inside every bracket pair (all common CJK and ASCII bracket styles),
counts how often each exact bracket-content string occurs, and writes a
frequency-sorted list plus one example title per entry. This does not decide
what is noise -- it only surfaces candidates for a human to review.

Usage: ./.venv/bin/python src/preprocessing/extract_bracket_content.py
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_PATH = PROJECT_ROOT / "data" / "raw" / "titles.jsonl"
MANIFEST_PATH = PROJECT_ROOT / "data" / "raw" / "manifest.json"
OUTPUT_DIR = PROJECT_ROOT / "data" / "interim" / "01_profile"

# (bracket style label, compiled non-nested "inside the pair" pattern)
BRACKET_PATTERNS = [
    ("()", re.compile(r"\(([^()]*)\)")),
    ("[]", re.compile(r"\[([^\[\]]*)\]")),
    ("{}", re.compile(r"\{([^{}]*)\}")),
    ("（）", re.compile(r"（([^（）]*)）")),
    ("【】", re.compile(r"【([^【】]*)】")),
    ("《》", re.compile(r"《([^《》]*)》")),
    ("〈〉", re.compile(r"〈([^〈〉]*)〉")),
    ("「」", re.compile(r"「([^「」]*)」")),
    ("『』", re.compile(r"『([^『』]*)』")),
    ("〔〕", re.compile(r"〔([^〔〕]*)〕")),
    ("＜＞", re.compile(r"＜([^＜＞]*)＞")),
    ("｛｝", re.compile(r"｛([^｛｝]*)｝")),
]


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    actual_sha256 = sha256_of(RAW_PATH)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if actual_sha256 != manifest["sha256"]:
        raise SystemExit(
            f"SHA-256 mismatch: raw file={actual_sha256} manifest={manifest['sha256']}. "
            "Refusing to run against a raw snapshot that does not match the frozen manifest."
        )

    # key = (bracket_style, content) -> count / example
    counts: Counter[tuple[str, str]] = Counter()
    examples: dict[tuple[str, str], tuple[str, str]] = {}  # -> (site, full_title)
    total_records = 0
    records_with_any_bracket = 0

    with RAW_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            total_records += 1
            rec = json.loads(line)
            title = rec["title"]
            site = rec["source_site"]
            hit_this_record = False
            for style, pattern in BRACKET_PATTERNS:
                for m in pattern.finditer(title):
                    content = m.group(1).strip()
                    if content == "":
                        continue
                    hit_this_record = True
                    key = (style, content)
                    counts[key] += 1
                    if key not in examples:
                        examples[key] = (site, title)
            if hit_this_record:
                records_with_any_bracket += 1

    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1]))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Plain list: just the bracket content itself, one per line, deduplicated,
    # sorted by frequency (most common first). No counts, types, or examples.
    plain_path = OUTPUT_DIR / "bracket_contents_only.txt"
    with plain_path.open("w", encoding="utf-8") as f:
        for (_style, content), _count in ordered:
            f.write(content + "\n")

    tsv_path = OUTPUT_DIR / "bracket_contents.tsv"
    with tsv_path.open("w", encoding="utf-8") as f:
        f.write("count\tbracket_style\tcontent\texample_site\texample_title\n")
        for (style, content), count in ordered:
            site, title = examples[(style, content)]
            row = [str(count), style, content, site, title]
            f.write("\t".join(x.replace("\t", " ").replace("\n", " ") for x in row) + "\n")

    md_path = OUTPUT_DIR / "bracket_contents_summary.md"
    by_style_totals = Counter()
    for (style, _content), count in counts.items():
        by_style_totals[style] += count

    lines = [
        "# 括号内文本抽取（供人工检查噪音，data/interim/01_profile）",
        "",
        f"生成时间：{datetime.now(timezone.utc).isoformat()}",
        f"输入文件：`data/raw/titles.jsonl`，SHA-256 `{actual_sha256}`",
        f"总记录数：{total_records}，含至少一个括号的记录：{records_with_any_bracket}"
        f"（{round(100*records_with_any_bracket/total_records,1)}%）",
        f"抽取到的括号片段（去重前）总次数：{sum(counts.values())}；"
        f"去重后不同片段数：{len(counts)}",
        "",
        "完整列表（按出现频次降序）见同目录 `bracket_contents.tsv`，"
        "可用表格软件按 `count` 列排序、筛选后人工检查。",
        "",
        "## 各括号类型的片段总出现次数",
        "",
        "| 括号类型 | 片段出现总次数 |",
        "|---|---:|",
    ]
    for style, total in sorted(by_style_totals.items(), key=lambda kv: -kv[1]):
        lines.append(f"| `{style}` | {total} |")

    lines += [
        "",
        "## 出现频次最高的 60 个片段（多为站点模板标签，如画质/时长/作品数）",
        "",
        "| 次数 | 括号类型 | 片段内容 | 示例站点 |",
        "|---:|---|---|---|",
    ]
    for (style, content), count in ordered[:60]:
        site, _title = examples[(style, content)]
        lines.append(f"| {count} | `{style}` | {content} | `{site}` |")

    singleton_count = sum(1 for _k, c in counts.items() if c == 1)
    lines += [
        "",
        f"## 只出现过一次的片段共 {singleton_count} 个",
        "",
        "这些是最可能藏有一次性噪音（比如误入的广告文案、无关备注）的部分，",
        "因为重复出现的站点模板标签会在上面的高频表里出现，不会混在这里。",
        "建议优先在 `bracket_contents.tsv` 里筛出 `count == 1` 的行人工过一遍。",
        "",
    ]

    md_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"input_sha256={actual_sha256}")
    print(f"total_records={total_records}")
    print(f"records_with_any_bracket={records_with_any_bracket}")
    print(f"unique_bracket_contents={len(counts)}")
    print(f"singleton_bracket_contents={singleton_count}")
    print(f"output_plain={plain_path.relative_to(PROJECT_ROOT)} sha256={sha256_of(plain_path)}")
    print(f"output_tsv={tsv_path.relative_to(PROJECT_ROOT)} sha256={sha256_of(tsv_path)}")
    print(f"output_md={md_path.relative_to(PROJECT_ROOT)} sha256={sha256_of(md_path)}")


if __name__ == "__main__":
    main()
