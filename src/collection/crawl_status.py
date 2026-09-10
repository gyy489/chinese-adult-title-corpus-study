"""文件用途: 查看 MySQL 标题采集状态, 用于扩量前后检查总量、队列和最近运行记录。"""

from __future__ import annotations

import argparse

from src.collection.crawler import load_config, select_sites
from src.collection.mysql_store import MySQLStore
from src.collection.project_paths import (
    DEFAULT_MYSQL_CONFIG,
    DEFAULT_SITE_CONFIG,
    PROJECT_ROOT,
    resolve_project_path,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show MySQL crawl progress and queue status.")
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
    parser.add_argument("--sites", default="", help="Optional comma-separated site names.")
    parser.add_argument(
        "--site-scope",
        default="",
        help="Optional site scope filter, for example 'community' or 'general'.",
    )
    parser.add_argument("--recent-runs", type=int, default=5, help="How many recent crawl runs to display.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    mysql_config_path = resolve_project_path(args.mysql_config)
    config_path = resolve_project_path(args.config)

    all_sites = load_config(config_path)
    requested_sites = [value.strip() for value in args.sites.split(",") if value.strip()]
    selected_sites = select_sites(all_sites, requested_sites, args.site_scope.strip())
    site_names = [site.name for site in selected_sites]

    store = MySQLStore(mysql_config_path)
    store.ensure_database_and_schema()
    total_titles = store.get_total_titles(site_names=site_names)
    site_title_counts = store.get_site_title_counts(site_names=site_names)
    queue_counts = store.get_queue_counts_by_site(site_names=site_names)
    recent_runs = store.get_recent_runs(limit=max(1, args.recent_runs))
    store.close()

    print(f"mysql_config={mysql_config_path}")
    print(f"selected_sites={site_names}")
    print(f"selected_scope={args.site_scope.strip() or 'all'}")
    print(f"total_titles={total_titles}")
    print("")
    print("title_counts_by_site")
    for site_name in site_names:
        print(f"- {site_name}: {site_title_counts.get(site_name, 0)}")
    print("")
    print("queue_counts_by_site")
    for site_name in site_names:
        site_queue = queue_counts.get(site_name, {"pending": 0, "processing": 0, "success": 0, "error": 0})
        print(
            f"- {site_name}: pending={site_queue['pending']}, processing={site_queue['processing']}, "
            f"success={site_queue['success']}, error={site_queue['error']}"
        )
    print("")
    print("recent_runs")
    for row in recent_runs:
        print(
            f"- id={row['id']} status={row['status']} label={row.get('run_label') or '-'} "
            f"started_at={row['started_at']} finished_at={row['finished_at']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
