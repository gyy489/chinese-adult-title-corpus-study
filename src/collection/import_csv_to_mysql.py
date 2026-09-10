"""Bootstrap the MySQL title store from an existing deduped CSV export."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from src.collection.mysql_store import MySQLStore
from src.collection.project_paths import DEFAULT_MYSQL_CONFIG, PROJECT_ROOT, resolve_project_path


def iter_non_comment_lines(path: Path):
    header_started = False
    with path.open(encoding="utf-8", newline="") as fh:
        for line in fh:
            if not line.strip():
                continue
            if not header_started and line.lstrip().startswith("#"):
                continue
            header_started = True
            yield line


def read_rows(path: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(iter_non_comment_lines(path)))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import existing CSV titles into MySQL storage.")
    parser.add_argument(
        "--mysql-config",
        default=str(DEFAULT_MYSQL_CONFIG.relative_to(PROJECT_ROOT)),
        help="Path to MySQL JSON config, relative to the project root unless absolute.",
    )
    parser.add_argument("--input", required=True, help="CSV file to import.")
    parser.add_argument("--run-label", default="bootstrap-csv-import", help="Run label recorded in crawl_runs.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = resolve_project_path(args.mysql_config)
    input_path = resolve_project_path(args.input)
    rows = read_rows(input_path)

    store = MySQLStore(config_path)
    store.ensure_database_and_schema()
    run_id = store.create_run(
        run_label=args.run_label,
        crawl_config={
            "mode": "csv_import",
            "input_path": str(input_path),
            "row_count": len(rows),
        },
        notes="Bootstrap import from existing CSV export.",
    )
    new_count, existing_count = store.upsert_titles(rows, run_id=run_id)
    summary = {
        "mode": "csv_import",
        "input_rows": len(rows),
        "new_titles": new_count,
        "existing_titles": existing_count,
    }
    store.finish_run(run_id, status="completed", summary=summary)
    store.close()

    print(f"run_id={run_id}")
    print(f"input_rows={len(rows)}")
    print(f"new_titles={new_count}")
    print(f"existing_titles={existing_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
