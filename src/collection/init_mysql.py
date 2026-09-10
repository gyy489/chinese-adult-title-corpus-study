"""Initialize the MySQL database and tables for resumable crawling."""

from __future__ import annotations

import argparse

from src.collection.mysql_store import MySQLStore
from src.collection.project_paths import DEFAULT_MYSQL_CONFIG, PROJECT_ROOT, resolve_project_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize MySQL storage for the title crawler.")
    parser.add_argument(
        "--mysql-config",
        default=str(DEFAULT_MYSQL_CONFIG.relative_to(PROJECT_ROOT)),
        help="Path to MySQL JSON config, relative to the project root unless absolute.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = resolve_project_path(args.mysql_config)

    store = MySQLStore(config_path)
    store.ensure_database_and_schema()
    queue_counts = store.get_queue_counts()
    title_counts = store.get_title_counts()
    database_name = store.config.database
    store.close()

    print(f"mysql_config={config_path}")
    print(f"database_initialized={database_name}")
    print(f"queue_counts={queue_counts}")
    print(f"title_counts={title_counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
