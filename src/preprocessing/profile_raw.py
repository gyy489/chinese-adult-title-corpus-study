"""Read-only profiling of data/raw/titles.jsonl (interim/01_profile layer).

Verifies the raw snapshot against data/raw/manifest.json, then reports field
completeness, duplicate structure, title-length distribution, character
composition, and a small set of anomaly flags. Never writes into data/raw/.

Usage: ./.venv/bin/python src/preprocessing/profile_raw.py
"""

from __future__ import annotations

import hashlib
import json
import statistics
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_PATH = PROJECT_ROOT / "data" / "raw" / "titles.jsonl"
MANIFEST_PATH = PROJECT_ROOT / "data" / "raw" / "manifest.json"
OUTPUT_DIR = PROJECT_ROOT / "data" / "interim" / "01_profile"

LATIN_RE = __import__("re").compile(r"[A-Za-z]")
DIGIT_RE = __import__("re").compile(r"[0-9]")
BRACKET_RE = __import__("re").compile(r"[()\[\]（）【】《》〈〉]")
CJK_RE = __import__("re").compile(r"[一-鿿]")
HTML_ENTITY_RE = __import__("re").compile(r"&(amp|nbsp|quot|lt|gt|#\d+);")
INVISIBLE_RE = __import__("re").compile(r"[​‌‍﻿]")

SHORT_TITLE_THRESHOLD = 4  # characters at/below this are flagged for review


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_records(path: Path) -> list[dict]:
    records = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def has_control_char(text: str) -> bool:
    return any(unicodedata.category(ch) == "Cc" for ch in text)


def profile(records: list[dict]) -> dict:
    n = len(records)
    titles = [r["title"] for r in records]
    sites = [r["source_site"] for r in records]

    # --- duplicate structure ---
    title_counts = Counter(titles)
    dup_values = {t: c for t, c in title_counts.items() if c > 1}
    dup_extra_rows = sum(c - 1 for c in dup_values.values())
    top_duplicates = sorted(dup_values.items(), key=lambda kv: -kv[1])[:15]

    title_to_sites = {}
    for r in records:
        title_to_sites.setdefault(r["title"], set()).add(r["source_site"])
    cross_site_dup_titles = {
        t: sorted(s) for t, s in title_to_sites.items() if len(s) > 1
    }

    url_counts = Counter(r["source_url"] for r in records)
    dup_urls = {u: c for u, c in url_counts.items() if c > 1}

    # --- length distribution ---
    lengths = [len(t) for t in titles]
    lengths_sorted = sorted(lengths)

    def pct(p: float) -> int:
        idx = min(len(lengths_sorted) - 1, int(len(lengths_sorted) * p))
        return lengths_sorted[idx]

    length_stats = {
        "min": min(lengths),
        "max": max(lengths),
        "mean": round(statistics.mean(lengths), 2),
        "median": statistics.median(lengths),
        "p01": pct(0.01),
        "p05": pct(0.05),
        "p95": pct(0.95),
        "p99": pct(0.99),
    }

    short_titles = [
        (r["title"], r["source_site"], r["source_url"])
        for r in records
        if len(r["title"]) <= SHORT_TITLE_THRESHOLD
    ]
    longest_titles = sorted(
        ((r["title"], r["source_site"]) for r in records),
        key=lambda x: -len(x[0]),
    )[:10]

    # --- character composition (overall + per site) ---
    def composition_stats(subset: list[dict]) -> dict:
        m = len(subset)
        if m == 0:
            return {}
        return {
            "n": m,
            "latin_pct": round(100 * sum(bool(LATIN_RE.search(r["title"])) for r in subset) / m, 1),
            "digit_pct": round(100 * sum(bool(DIGIT_RE.search(r["title"])) for r in subset) / m, 1),
            "bracket_pct": round(100 * sum(bool(BRACKET_RE.search(r["title"])) for r in subset) / m, 1),
            "no_cjk_pct": round(100 * sum(not CJK_RE.search(r["title"]) for r in subset) / m, 1),
        }

    composition_overall = composition_stats(records)
    composition_by_site = {
        site: composition_stats([r for r in records if r["source_site"] == site])
        for site in sorted(set(sites))
    }

    # --- anomaly flags ---
    no_cjk_records = [
        (r["title"], r["source_site"]) for r in records if not CJK_RE.search(r["title"])
    ]
    control_char_records = [
        (r["title"], r["source_site"]) for r in records if has_control_char(r["title"])
    ]
    replacement_char_records = [
        (r["title"], r["source_site"]) for r in records if "�" in r["title"]
    ]
    invisible_char_records = [
        (r["title"], r["source_site"]) for r in records if INVISIBLE_RE.search(r["title"])
    ]
    html_entity_records = [
        (r["title"], r["source_site"]) for r in records if HTML_ENTITY_RE.search(r["title"])
    ]
    whitespace_edge_records = [
        (r["title"], r["source_site"])
        for r in records
        if r["title"] != r["title"].strip() or "  " in r["title"]
    ]

    # --- crawl_time parse check ---
    parse_failures = []
    parsed_times = []
    for r in records:
        try:
            parsed_times.append(datetime.fromisoformat(r["crawl_time"]))
        except ValueError:
            parse_failures.append((r["title"], r["crawl_time"]))

    return {
        "record_count": n,
        "site_counts": dict(Counter(sites)),
        "duplicates": {
            "duplicate_title_values": len(dup_values),
            "duplicate_title_extra_rows": dup_extra_rows,
            "top_duplicate_titles": top_duplicates,
            "cross_site_duplicate_title_count": len(cross_site_dup_titles),
            "cross_site_duplicate_titles_sample": dict(
                list(cross_site_dup_titles.items())[:15]
            ),
            "duplicate_source_url_count": len(dup_urls),
        },
        "length": {
            "stats": length_stats,
            "short_title_threshold": SHORT_TITLE_THRESHOLD,
            "short_title_count": len(short_titles),
            "short_title_examples": short_titles[:20],
            "longest_titles": longest_titles,
        },
        "composition": {
            "overall": composition_overall,
            "by_site": composition_by_site,
        },
        "anomalies": {
            "no_cjk_count": len(no_cjk_records),
            "no_cjk_examples": no_cjk_records[:20],
            "control_char_count": len(control_char_records),
            "control_char_examples": control_char_records[:20],
            "replacement_char_count": len(replacement_char_records),
            "replacement_char_examples": replacement_char_records[:20],
            "invisible_char_count": len(invisible_char_records),
            "invisible_char_examples": invisible_char_records[:20],
            "html_entity_count": len(html_entity_records),
            "html_entity_examples": html_entity_records[:20],
            "whitespace_edge_count": len(whitespace_edge_records),
            "whitespace_edge_examples": whitespace_edge_records[:20],
        },
        "crawl_time": {
            "parse_failure_count": len(parse_failures),
            "parse_failure_examples": parse_failures[:20],
            "min": min(parsed_times).isoformat() if parsed_times else None,
            "max": max(parsed_times).isoformat() if parsed_times else None,
        },
    }


def render_markdown(report: dict, input_sha256: str) -> str:
    c = report["composition"]["overall"]
    lines = [
        "# 原始数据审计报告（interim/01_profile）",
        "",
        f"生成时间：{datetime.now(timezone.utc).isoformat()}",
        f"输入文件：`data/raw/titles.jsonl`，SHA-256 `{input_sha256}`",
        f"记录数：{report['record_count']}",
        "",
        "## 站点分布",
        "",
        "| 站点 | 记录数 |",
        "|---|---:|",
    ]
    for site, cnt in sorted(report["site_counts"].items(), key=lambda kv: -kv[1]):
        lines.append(f"| `{site}` | {cnt} |")

    d = report["duplicates"]
    lines += [
        "",
        "## 重复结构",
        "",
        f"- 重复标题值：{d['duplicate_title_values']}（额外记录 {d['duplicate_title_extra_rows']} 条）",
        f"- 跨站完全相同的标题：{d['cross_site_duplicate_title_count']} 个",
        f"- 重复 `source_url`：{d['duplicate_source_url_count']} 个（应为 0，若非 0 需要排查采集/导出流程）",
        "",
        "最高频重复标题（前 15）：",
        "",
    ]
    for title, cnt in d["top_duplicate_titles"]:
        lines.append(f"- `{cnt}` 次：{title}")

    lg = report["length"]["stats"]
    lines += [
        "",
        "## 标题长度（字符数）分布",
        "",
        f"- min={lg['min']}, p01={lg['p01']}, p05={lg['p05']}, median={lg['median']}, "
        f"mean={lg['mean']}, p95={lg['p95']}, p99={lg['p99']}, max={lg['max']}",
        f"- 短标题（长度 ≤ {report['length']['short_title_threshold']}）：{report['length']['short_title_count']} 条",
        "",
        "最长标题样本（前 10）：",
        "",
    ]
    for title, site in report["length"]["longest_titles"]:
        lines.append(f"- [{site}] {title[:80]}{'…' if len(title) > 80 else ''}（{len(title)} 字）")

    lines += [
        "",
        "## 字符构成（整体）",
        "",
        f"- 含拉丁字母：{c['latin_pct']}%",
        f"- 含数字：{c['digit_pct']}%",
        f"- 含括号类符号：{c['bracket_pct']}%",
        f"- 不含任何汉字：{c['no_cjk_pct']}%",
        "",
        "## 字符构成（按站点）",
        "",
        "| 站点 | n | 含拉丁字母 | 含数字 | 含括号 | 不含汉字 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for site, stats in report["composition"]["by_site"].items():
        lines.append(
            f"| `{site}` | {stats['n']} | {stats['latin_pct']}% | {stats['digit_pct']}% | "
            f"{stats['bracket_pct']}% | {stats['no_cjk_pct']}% |"
        )

    a = report["anomalies"]
    lines += [
        "",
        "## 异常标记",
        "",
        f"- 不含任何汉字的标题：{a['no_cjk_count']} 条",
        f"- 含 Unicode 控制字符：{a['control_char_count']} 条",
        f"- 含 U+FFFD 替换字符（编码损坏迹象）：{a['replacement_char_count']} 条",
        f"- 含零宽字符/BOM：{a['invisible_char_count']} 条",
        f"- 含未转义 HTML 实体（如 &amp;）：{a['html_entity_count']} 条",
        f"- 首尾有空白或含连续空格：{a['whitespace_edge_count']} 条",
        "",
    ]
    if a["no_cjk_count"]:
        lines.append("不含汉字标题样本：")
        for title, site in a["no_cjk_examples"]:
            lines.append(f"- [{site}] {title}")
        lines.append("")
    if a["control_char_count"]:
        lines.append("含控制字符标题样本：")
        for title, site in a["control_char_examples"]:
            lines.append(f"- [{site}] {title!r}")
        lines.append("")
    if a["html_entity_count"]:
        lines.append("含 HTML 实体标题样本：")
        for title, site in a["html_entity_examples"]:
            lines.append(f"- [{site}] {title}")
        lines.append("")
    if a["whitespace_edge_count"]:
        lines.append("空白异常标题样本：")
        for title, site in a["whitespace_edge_examples"][:10]:
            lines.append(f"- [{site}] {title!r}")
        lines.append("")

    ct = report["crawl_time"]
    lines += [
        "## crawl_time 校验",
        "",
        f"- 无法解析为 ISO 时间的记录：{ct['parse_failure_count']}",
        f"- 时间范围：{ct['min']} 至 {ct['max']}",
        "",
        "## 结论（本轮不做清洗决定，只报告现象）",
        "",
        "以上结果只用于识别需要在清洗阶段设计规则的现象；是否剔除、如何处理，",
        "需按 `docs/research_protocol.md` 第 3 节逐条记录理由、命中率、样本和",
        "敏感性分析计划后再执行，不在本次审计中直接修改数据。",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    actual_sha256 = sha256_of(RAW_PATH)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if actual_sha256 != manifest["sha256"]:
        raise SystemExit(
            f"SHA-256 mismatch: raw file={actual_sha256} manifest={manifest['sha256']}. "
            "Refusing to profile a raw snapshot that does not match the frozen manifest."
        )

    records = load_records(RAW_PATH)
    if len(records) != manifest["records"]:
        raise SystemExit(
            f"Record count mismatch: file has {len(records)}, manifest says {manifest['records']}."
        )

    report = profile(records)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUTPUT_DIR / "raw_profile.json"
    md_path = OUTPUT_DIR / "raw_profile_report.md"

    run_metadata = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "input_path": str(RAW_PATH.relative_to(PROJECT_ROOT)),
        "input_sha256": actual_sha256,
        "input_record_count": len(records),
        "script": str(Path(__file__).relative_to(PROJECT_ROOT)),
    }
    full_output = {"run_metadata": run_metadata, "report": report}
    json_path.write_text(
        json.dumps(full_output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md_path.write_text(render_markdown(report, actual_sha256), encoding="utf-8")

    print(f"input_sha256={actual_sha256}")
    print(f"record_count={len(records)}")
    print(f"output_json={json_path.relative_to(PROJECT_ROOT)} sha256={sha256_of(json_path)}")
    print(f"output_md={md_path.relative_to(PROJECT_ROOT)} sha256={sha256_of(md_path)}")


if __name__ == "__main__":
    main()
