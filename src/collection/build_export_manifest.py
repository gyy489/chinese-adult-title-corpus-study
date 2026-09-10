"""文件用途: 为 MySQL 数据导出目录生成 manifest.json（字段/格式仿照
`data/raw/manifest.json`），供 round1+round2 合并快照等非冻结导出使用。

只读取导出目录里已经存在的 `titles.jsonl`/`titles.csv`（由
`src/collection/export_mysql_dataset.py` 产出，本脚本不修改也不重新导出），
计算记录数、字段缺失、站点分布、采集时间范围、精确重复标题统计和文件
SHA-256，写回同一目录的 `manifest.json`。绝不触碰 `data/raw/`。

Usage:
    ./.venv/bin/python src/collection/build_export_manifest.py \
        --export-dir data/exports/2026-08-02_round1_round2_combined \
        --snapshot-id 2026-08-02-round1-round2-combined
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Round membership is fixed by which crawl produced each site (round1 =
# the 4 sites frozen in data/raw/titles.jsonl; round2 = the 6 new sites
# crawled 2026-08-02). This mapping is descriptive metadata for the
# manifest only -- it does not affect export contents.
ROUND1_SITE_NAMES = ["source_01", "source_02", "source_03", "source_04"]
ROUND2_SITE_NAMES = ["source_05", "source_06", "source_07", "source_08", "source_09", "source_10"]

FIELDS = ["title", "source_url", "source_site", "listing_url", "crawl_time"]


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export-dir", required=True, help="Export directory relative to project root.")
    parser.add_argument("--snapshot-id", required=True, help="Human-readable snapshot identifier.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    export_dir = (PROJECT_ROOT / args.export_dir).resolve()
    titles_path = export_dir / "titles.jsonl"
    csv_path = export_dir / "titles.csv"

    records = []
    with titles_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    missing_field_counts = {field: 0 for field in FIELDS}
    for rec in records:
        for field in FIELDS:
            if rec.get(field) in (None, ""):
                missing_field_counts[field] += 1

    source_counts = dict(sorted(Counter(r["source_site"] for r in records).items()))
    crawl_times = sorted(r["crawl_time"] for r in records)
    unique_urls = {r["source_url"] for r in records}
    title_counts = Counter(r["title"] for r in records)
    dup_values = {t: c for t, c in title_counts.items() if c > 1}
    dup_extra_rows = sum(c - 1 for c in dup_values.values())

    round1_n = sum(c for s, c in source_counts.items() if s in ROUND1_SITE_NAMES)
    round2_n = sum(c for s, c in source_counts.items() if s in ROUND2_SITE_NAMES)
    unknown_sites = sorted(set(source_counts) - set(ROUND1_SITE_NAMES) - set(ROUND2_SITE_NAMES))

    jsonl_sha256 = sha256_of(titles_path)
    manifest = {
        "snapshot_id": args.snapshot_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_by": "src/collection/build_export_manifest.py",
        "file": "titles.jsonl",
        "format": "UTF-8 JSON Lines",
        "bytes": titles_path.stat().st_size,
        "records": len(records),
        "sha256": jsonl_sha256,
        "fields": FIELDS,
        "missing_field_counts": missing_field_counts,
        "source_counts": source_counts,
        "round1_site_names": ROUND1_SITE_NAMES,
        "round2_site_names": ROUND2_SITE_NAMES,
        "round1_record_count": round1_n,
        "round2_record_count": round2_n,
        "crawl_time_min": crawl_times[0] if crawl_times else None,
        "crawl_time_max": crawl_times[-1] if crawl_times else None,
        "unique_source_urls": len(unique_urls),
        "duplicate_title_extra_rows": dup_extra_rows,
        "duplicate_title_values": len(dup_values),
        "csv_file": {
            "file": "titles.csv",
            "bytes": csv_path.stat().st_size if csv_path.exists() else None,
            "sha256": sha256_of(csv_path) if csv_path.exists() else None,
        },
        "notes": [
            "This is a NEW, independent snapshot combining round1 (frozen "
            "data/raw/titles.jsonl, 4 sites, 25191 records, unmodified by "
            "this export) with round2 (6 new sites), as currently stored in "
            "the title_research MySQL database. It does NOT modify, "
            "overwrite, or replace data/raw/titles.jsonl in any way.",
            "Exported via src/collection/export_mysql_dataset.py with no "
            "site/limit filters (all 10 sites), row order = MySQL "
            "raw_titles.id ascending (insertion order), not run order or "
            "crawl_time order.",
            "No cleaning, deduplication, tokenization, or exclusion rule has "
            "been applied to this file. See "
            "data/interim/10_combined_deduplicated/ for the cross-round "
            "exact-duplicate flagging and rule-016 layer built on top of "
            "this export.",
        ],
    }
    if unknown_sites:
        manifest["notes"].append(
            "WARNING: source_site values present that are not in either "
            f"round's known site list: {unknown_sites}"
        )

    manifest_path = export_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"wrote {manifest_path.relative_to(PROJECT_ROOT)}")
    print(f"records={len(records)} sha256={jsonl_sha256}")
    print(f"round1_record_count={round1_n} round2_record_count={round2_n}")
    if unknown_sites:
        print(f"WARNING unknown_sites={unknown_sites}")


if __name__ == "__main__":
    main()
