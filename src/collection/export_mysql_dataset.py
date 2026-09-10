"""文件用途: 将 MySQL 主库中的标题数据导出为 CSV 和 JSONL 兼容文件。"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from src.collection.crawler import load_config, select_sites
from src.collection.mysql_store import MySQLStore
from src.collection.project_paths import (
    DEFAULT_EXPORT_DIR,
    DEFAULT_MYSQL_CONFIG,
    DEFAULT_SITE_CONFIG,
    PROJECT_ROOT,
    resolve_project_path,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export MySQL title data to CSV/JSONL files.")
    parser.add_argument(
        "--mysql-config",
        default=str(DEFAULT_MYSQL_CONFIG.relative_to(PROJECT_ROOT)),
        help="Path to MySQL JSON config, relative to the project root unless absolute.",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_SITE_CONFIG.relative_to(PROJECT_ROOT)),
        help="Path to site config JSON, relative to the project root unless absolute.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_EXPORT_DIR.relative_to(PROJECT_ROOT)),
        help="Directory for exports, relative to the project root unless absolute.",
    )
    parser.add_argument("--limit", type=int, default=0, help="Optional export row limit.")
    parser.add_argument("--sites", default="", help="Optional comma-separated site names.")
    parser.add_argument(
        "--site-scope",
        default="",
        help="Optional site scope filter, for example 'community' or 'general'.",
    )
    return parser.parse_args()


def write_jsonl(path: Path, items: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for item in items:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")


def write_csv(path: Path, items: list[dict[str, str]]) -> None:
    fieldnames = ["title", "source_url", "source_site", "listing_url", "crawl_time"]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(items)


def main() -> int:
    args = parse_args()
    config_path = resolve_project_path(args.mysql_config)
    site_config_path = resolve_project_path(args.config)
    output_dir = resolve_project_path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    all_sites = load_config(site_config_path)
    requested_sites = [value.strip() for value in args.sites.split(",") if value.strip()]
    selected_sites = select_sites(all_sites, requested_sites, args.site_scope.strip())
    sites = [site.name for site in selected_sites] or None
    limit = args.limit if args.limit > 0 else None

    store = MySQLStore(config_path)
    store.ensure_database_and_schema()
    rows = store.fetch_export_rows(site_names=sites, limit=limit)
    store.close()

    write_jsonl(output_dir / "titles.jsonl", rows)
    write_csv(output_dir / "titles.csv", rows)

    print(f"exported_rows={len(rows)}")
    print(f"output_dir={output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
