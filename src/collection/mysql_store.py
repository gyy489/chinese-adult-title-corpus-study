"""文件用途: 提供 MySQL 主存储, 支撑大规模标题采集、队列续跑和状态恢复。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pymysql
from pymysql.cursors import DictCursor


DEFAULT_CONFIG = {
    "host": "127.0.0.1",
    "port": 3306,
    "user": "root",
    "password": "",
    "database": "title_research",
    "charset": "utf8mb4",
    "unix_socket": "/tmp/mysql.sock",
    "connect_timeout": 10,
}


@dataclass
class MySQLConfig:
    host: str
    port: int
    user: str
    password: str
    database: str
    charset: str
    unix_socket: str | None
    connect_timeout: int


def utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def normalize_dt(value: str | datetime | None) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    if not value:
        return utc_now_naive()
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_mysql_config(path: Path) -> MySQLConfig:
    payload = dict(DEFAULT_CONFIG)
    if path.exists():
        file_payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(file_payload, dict):
            raise ValueError(f"MySQL config must be a JSON object: {path}")
        payload.update({key: value for key, value in file_payload.items() if not str(key).startswith("_")})
    return MySQLConfig(
        host=str(payload["host"]),
        port=int(payload["port"]),
        user=str(payload["user"]),
        password=str(payload.get("password", "")),
        database=str(payload["database"]),
        charset=str(payload.get("charset", "utf8mb4")),
        unix_socket=str(payload["unix_socket"]) if payload.get("unix_socket") else None,
        connect_timeout=int(payload.get("connect_timeout", 10)),
    )


class MySQLStore:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self.config = load_mysql_config(config_path)
        self.connection: pymysql.connections.Connection | None = None

    def _connect_kwargs(self, *, include_database: bool) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "user": self.config.user,
            "password": self.config.password,
            "charset": self.config.charset,
            "connect_timeout": self.config.connect_timeout,
            "autocommit": False,
            "cursorclass": DictCursor,
        }
        if self.config.unix_socket:
            kwargs["unix_socket"] = self.config.unix_socket
        else:
            kwargs["host"] = self.config.host
            kwargs["port"] = self.config.port
        if include_database:
            kwargs["database"] = self.config.database
        return kwargs

    def ensure_database_and_schema(self) -> None:
        admin_connection = pymysql.connect(**self._connect_kwargs(include_database=False))
        try:
            with admin_connection.cursor() as cursor:
                cursor.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{self.config.database}` "
                    f"CHARACTER SET {self.config.charset} COLLATE {self.config.charset}_unicode_ci"
                )
            admin_connection.commit()
        finally:
            admin_connection.close()

        self.connection = pymysql.connect(**self._connect_kwargs(include_database=True))
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS crawl_runs (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    run_label VARCHAR(255) NULL,
                    started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    finished_at DATETIME NULL,
                    status VARCHAR(32) NOT NULL,
                    storage_backend VARCHAR(32) NOT NULL DEFAULT 'mysql',
                    config_path VARCHAR(512) NULL,
                    crawl_config_json JSON NULL,
                    summary_json JSON NULL,
                    notes TEXT NULL,
                    KEY idx_runs_status (status),
                    KEY idx_runs_started_at (started_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS crawl_queue (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    site_name VARCHAR(128) NOT NULL,
                    page_url TEXT NOT NULL,
                    page_url_hash CHAR(64) NOT NULL,
                    discovered_from TEXT NULL,
                    page_number INT NULL,
                    status VARCHAR(16) NOT NULL DEFAULT 'pending',
                    attempts INT NOT NULL DEFAULT 0,
                    last_error TEXT NULL,
                    first_discovered_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_enqueued_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_attempt_at DATETIME NULL,
                    last_success_at DATETIME NULL,
                    last_run_id BIGINT NULL,
                    UNIQUE KEY uq_queue_site_page (site_name, page_url_hash),
                    KEY idx_queue_status (site_name, status, attempts, id),
                    KEY idx_queue_last_run (last_run_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS raw_titles (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    canonical_key CHAR(64) NOT NULL,
                    source_site VARCHAR(128) NOT NULL,
                    title TEXT NOT NULL,
                    normalized_title TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    source_url_hash CHAR(64) NOT NULL,
                    listing_url TEXT NOT NULL,
                    crawl_time DATETIME NOT NULL,
                    first_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    first_run_id BIGINT NULL,
                    last_run_id BIGINT NULL,
                    seen_count INT NOT NULL DEFAULT 1,
                    title_length INT NOT NULL DEFAULT 0,
                    UNIQUE KEY uq_raw_canonical (canonical_key),
                    KEY idx_raw_site (source_site),
                    KEY idx_raw_source_hash (source_url_hash),
                    KEY idx_raw_last_seen (last_seen_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
        self.connection.commit()

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def create_run(self, run_label: str | None, crawl_config: dict[str, Any], notes: str = "") -> int:
        assert self.connection is not None
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO crawl_runs (
                    run_label,
                    started_at,
                    status,
                    config_path,
                    crawl_config_json,
                    notes
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    run_label or None,
                    utc_now_naive(),
                    "running",
                    str(self.config_path),
                    json.dumps(crawl_config, ensure_ascii=False),
                    notes or None,
                ),
            )
            run_id = int(cursor.lastrowid)
        self.connection.commit()
        return run_id

    def finish_run(self, run_id: int, status: str, summary: dict[str, Any]) -> None:
        assert self.connection is not None
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE crawl_runs
                SET status = %s,
                    finished_at = %s,
                    summary_json = %s
                WHERE id = %s
                """,
                (
                    status,
                    utc_now_naive(),
                    json.dumps(summary, ensure_ascii=False),
                    run_id,
                ),
            )
        self.connection.commit()

    def enqueue_page(self, site_name: str, page_url: str, discovered_from: str | None, page_number: int | None) -> None:
        assert self.connection is not None
        page_hash = sha256_text(page_url)
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO crawl_queue (
                    site_name,
                    page_url,
                    page_url_hash,
                    discovered_from,
                    page_number,
                    status,
                    first_discovered_at,
                    last_enqueued_at
                ) VALUES (%s, %s, %s, %s, %s, 'pending', %s, %s)
                ON DUPLICATE KEY UPDATE
                    last_enqueued_at = VALUES(last_enqueued_at),
                    discovered_from = COALESCE(crawl_queue.discovered_from, VALUES(discovered_from)),
                    page_number = COALESCE(crawl_queue.page_number, VALUES(page_number))
                """,
                (
                    site_name,
                    page_url,
                    page_hash,
                    discovered_from,
                    page_number,
                    utc_now_naive(),
                    utc_now_naive(),
                ),
            )
        self.connection.commit()

    def seed_start_urls(self, sites: list[Any]) -> None:
        for site in sites:
            for start_url in site.start_urls:
                self.enqueue_page(site.name, start_url, discovered_from=None, page_number=None)

    def claim_next_page(self, site_name: str, run_id: int, max_attempts: int) -> dict[str, Any] | None:
        assert self.connection is not None
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, site_name, page_url, discovered_from, page_number, attempts, status
                FROM crawl_queue
                WHERE site_name = %s
                  AND status IN ('pending', 'error')
                  AND attempts < %s
                ORDER BY
                  CASE status WHEN 'pending' THEN 0 ELSE 1 END,
                  attempts ASC,
                  id ASC
                LIMIT 1
                FOR UPDATE
                """,
                (site_name, max_attempts),
            )
            row = cursor.fetchone()
            if row is None:
                self.connection.rollback()
                return None

            cursor.execute(
                """
                UPDATE crawl_queue
                SET status = 'processing',
                    attempts = attempts + 1,
                    last_attempt_at = %s,
                    last_run_id = %s,
                    last_error = NULL
                WHERE id = %s
                """,
                (utc_now_naive(), run_id, row["id"]),
            )
        self.connection.commit()
        return row

    def mark_page_success(self, page_id: int, run_id: int) -> None:
        assert self.connection is not None
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE crawl_queue
                SET status = 'success',
                    last_success_at = %s,
                    last_run_id = %s,
                    last_error = NULL
                WHERE id = %s
                """,
                (utc_now_naive(), run_id, page_id),
            )
        self.connection.commit()

    def mark_page_error(self, page_id: int, run_id: int, error_message: str) -> None:
        assert self.connection is not None
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE crawl_queue
                SET status = 'error',
                    last_run_id = %s,
                    last_error = %s
                WHERE id = %s
                """,
                (run_id, error_message[:4000], page_id),
            )
        self.connection.commit()

    def upsert_titles(self, items: list[dict[str, str]], run_id: int) -> tuple[int, int]:
        assert self.connection is not None
        new_count = 0
        existing_count = 0
        now = utc_now_naive()
        with self.connection.cursor() as cursor:
            for item in items:
                source_site = str(item["source_site"])
                source_url = str(item["source_url"])
                title = str(item["title"])
                canonical_source = source_url or title
                canonical_key = sha256_text(f"{source_site}\n{canonical_source}")
                source_url_hash = sha256_text(source_url)
                normalized_title = " ".join(title.split())
                crawl_time = normalize_dt(item.get("crawl_time"))
                cursor.execute(
                    """
                    INSERT INTO raw_titles (
                        canonical_key,
                        source_site,
                        title,
                        normalized_title,
                        source_url,
                        source_url_hash,
                        listing_url,
                        crawl_time,
                        first_seen_at,
                        last_seen_at,
                        first_run_id,
                        last_run_id,
                        seen_count,
                        title_length
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s)
                    ON DUPLICATE KEY UPDATE
                        title = VALUES(title),
                        normalized_title = VALUES(normalized_title),
                        listing_url = VALUES(listing_url),
                        crawl_time = VALUES(crawl_time),
                        last_seen_at = VALUES(last_seen_at),
                        last_run_id = VALUES(last_run_id),
                        seen_count = raw_titles.seen_count + 1,
                        title_length = VALUES(title_length)
                    """,
                    (
                        canonical_key,
                        source_site,
                        title,
                        normalized_title,
                        source_url,
                        source_url_hash,
                        str(item["listing_url"]),
                        crawl_time,
                        now,
                        now,
                        run_id,
                        run_id,
                        len(title),
                    ),
                )
                if cursor.rowcount == 1:
                    new_count += 1
                else:
                    existing_count += 1
        self.connection.commit()
        return new_count, existing_count

    def fetch_export_rows(self, *, site_names: list[str] | None = None, limit: int | None = None) -> list[dict[str, str]]:
        assert self.connection is not None
        sql = """
            SELECT
                title,
                source_url,
                source_site,
                listing_url,
                DATE_FORMAT(crawl_time, '%%Y-%%m-%%dT%%H:%%i:%%s') AS crawl_time
            FROM raw_titles
        """
        params: list[Any] = []
        if site_names:
            placeholders = ", ".join(["%s"] * len(site_names))
            sql += f" WHERE source_site IN ({placeholders})"
            params.extend(site_names)
        sql += " ORDER BY id ASC"
        if limit is not None:
            sql += " LIMIT %s"
            params.append(int(limit))
        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        return [
            {
                "title": str(row["title"]),
                "source_url": str(row["source_url"]),
                "source_site": str(row["source_site"]),
                "listing_url": str(row["listing_url"]),
                "crawl_time": str(row["crawl_time"]),
            }
            for row in rows
        ]

    def get_queue_counts(self) -> dict[str, int]:
        assert self.connection is not None
        counts = {"pending": 0, "processing": 0, "success": 0, "error": 0}
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM crawl_queue
                GROUP BY status
                """
            )
            for row in cursor.fetchall():
                counts[str(row["status"])] = int(row["count"])
        return counts

    def get_queue_counts_by_site(self, site_names: list[str] | None = None) -> dict[str, dict[str, int]]:
        assert self.connection is not None
        sql = """
            SELECT site_name, status, COUNT(*) AS count
            FROM crawl_queue
        """
        params: list[Any] = []
        if site_names:
            placeholders = ", ".join(["%s"] * len(site_names))
            sql += f" WHERE site_name IN ({placeholders})"
            params.extend(site_names)
        sql += " GROUP BY site_name, status ORDER BY site_name ASC"
        by_site: dict[str, dict[str, int]] = {}
        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            for row in cursor.fetchall():
                site_name = str(row["site_name"])
                status = str(row["status"])
                if site_name not in by_site:
                    by_site[site_name] = {"pending": 0, "processing": 0, "success": 0, "error": 0}
                by_site[site_name][status] = int(row["count"])
        return by_site

    def get_title_counts(self) -> dict[str, int]:
        assert self.connection is not None
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS count FROM raw_titles")
            total_titles = int(cursor.fetchone()["count"])
            cursor.execute("SELECT COUNT(DISTINCT source_site) AS count FROM raw_titles")
            source_sites = int(cursor.fetchone()["count"])
        return {"total_titles": total_titles, "source_sites": source_sites}

    def get_total_titles(self, site_names: list[str] | None = None) -> int:
        assert self.connection is not None
        sql = "SELECT COUNT(*) AS count FROM raw_titles"
        params: list[Any] = []
        if site_names:
            placeholders = ", ".join(["%s"] * len(site_names))
            sql += f" WHERE source_site IN ({placeholders})"
            params.extend(site_names)
        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            return int(cursor.fetchone()["count"])

    def get_site_title_counts(self, site_names: list[str] | None = None) -> dict[str, int]:
        assert self.connection is not None
        sql = """
            SELECT source_site, COUNT(*) AS count
            FROM raw_titles
        """
        params: list[Any] = []
        if site_names:
            placeholders = ", ".join(["%s"] * len(site_names))
            sql += f" WHERE source_site IN ({placeholders})"
            params.extend(site_names)
        sql += " GROUP BY source_site ORDER BY source_site ASC"
        counts: dict[str, int] = {}
        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            for row in cursor.fetchall():
                counts[str(row["source_site"])] = int(row["count"])
        return counts

    def get_recent_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        assert self.connection is not None
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id,
                    run_label,
                    status,
                    storage_backend,
                    started_at,
                    finished_at
                FROM crawl_runs
                ORDER BY id DESC
                LIMIT %s
                """,
                (int(limit),),
            )
            rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def requeue_errors(self, site_name: str | None = None) -> int:
        assert self.connection is not None
        sql = "UPDATE crawl_queue SET status = 'pending', last_error = NULL WHERE status = 'error'"
        params: list[Any] = []
        if site_name:
            sql += " AND site_name = %s"
            params.append(site_name)
        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            updated = int(cursor.rowcount)
        self.connection.commit()
        return updated

    def requeue_stale_processing(
        self,
        *,
        older_than_minutes: int,
        site_names: list[str] | None = None,
    ) -> int:
        assert self.connection is not None
        cutoff = utc_now_naive() - timedelta(minutes=max(0, older_than_minutes))
        sql = """
            UPDATE crawl_queue
            SET status = 'pending',
                last_error = NULL
            WHERE status = 'processing'
              AND (last_attempt_at IS NULL OR last_attempt_at <= %s)
        """
        params: list[Any] = [cutoff]
        if site_names:
            placeholders = ", ".join(["%s"] * len(site_names))
            sql += f" AND site_name IN ({placeholders})"
            params.extend(site_names)
        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            updated = int(cursor.rowcount)
        self.connection.commit()
        return updated

    def mark_stale_running_runs_as_interrupted(self, *, older_than_minutes: int) -> int:
        assert self.connection is not None
        cutoff = utc_now_naive() - timedelta(minutes=max(0, older_than_minutes))
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE crawl_runs
                SET status = 'interrupted',
                    finished_at = %s
                WHERE status = 'running'
                  AND finished_at IS NULL
                  AND started_at <= %s
                """,
                (utc_now_naive(), cutoff),
            )
            updated = int(cursor.rowcount)
        self.connection.commit()
        return updated
