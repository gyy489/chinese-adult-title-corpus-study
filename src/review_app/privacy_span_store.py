"""SQLite persistence for exact-span privacy review."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from itertools import pairwise
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data/interim/75_gendered_visibility_privacy_span_review_v1"
QUEUE = DATA / "private/span_review_queue.csv"
MANIFEST = DATA / "manifest.json"
EXPECTED_RECORDS = 9
ACTIONS = {"redact", "retain", "exclude"}
REPLACEMENTS = {"某人", "某账号", "某机构", "某地", ""}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def secure_file(path: Path) -> None:
    if path.exists():
        os.chmod(path, 0o600)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


class PrivacySpanReviewStore:
    def __init__(
        self,
        reviewer_id: str,
        db_path: Path,
        queue_path: Path = QUEUE,
        manifest_path: Path = MANIFEST,
        export_root: Path | None = None,
    ) -> None:
        if (
            not reviewer_id
            or len(reviewer_id) > 64
            or not all(char.isalnum() or char in "_-" for char in reviewer_id)
        ):
            raise ValueError("reviewer id仅可包含字母、数字、_、-")
        self.reviewer_id = reviewer_id
        self.db_path = Path(db_path)
        self.queue_path = Path(queue_path)
        self.manifest_path = Path(manifest_path)
        self.export_root = Path(export_root) if export_root else DATA / "exports"

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _secure_database(self) -> None:
        secure_file(self.db_path)
        secure_file(Path(str(self.db_path) + "-wal"))
        secure_file(Path(str(self.db_path) + "-shm"))

    def initialize(self) -> None:
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        for key, expected in manifest.get("inputs", {}).items():
            path = ROOT / key
            if not path.is_file() or sha256_file(path) != expected:
                raise RuntimeError(f"片段审核输入SHA-256不一致: {key}")
        try:
            queue_key = self.queue_path.resolve().relative_to(ROOT.resolve()).as_posix()
        except ValueError:
            queue_key = self.queue_path.resolve().as_posix()
        if manifest.get("outputs", {}).get(queue_key) != sha256_file(self.queue_path):
            raise RuntimeError("片段审核队列SHA-256与manifest不一致")
        rows = read_csv(self.queue_path)
        if len(rows) != EXPECTED_RECORDS:
            raise RuntimeError("边界复核队列不是9条")
        if len({row["span_review_id"] for row in rows}) != EXPECTED_RECORDS:
            raise RuntimeError("片段审核ID不唯一")

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.db_path.parent, 0o700)
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS records(
                    span_review_id TEXT PRIMARY KEY,
                    position INTEGER UNIQUE NOT NULL,
                    confirmed_title_position INTEGER UNIQUE NOT NULL,
                    title_sha256 TEXT UNIQUE NOT NULL,
                    title TEXT NOT NULL,
                    sampling_track TEXT NOT NULL,
                    queue_sources TEXT NOT NULL,
                    prior_note TEXT NOT NULL,
                    exception_reason TEXT NOT NULL,
                    candidate_spans_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS metadata(
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS annotations(
                    reviewer_id TEXT NOT NULL,
                    span_review_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    spans_json TEXT NOT NULL,
                    note TEXT NOT NULL,
                    status TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL,
                    PRIMARY KEY(reviewer_id,span_review_id)
                );
                CREATE TABLE IF NOT EXISTS events(
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reviewer_id TEXT NOT NULL,
                    span_review_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    old_json TEXT,
                    new_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    event_time TEXT NOT NULL
                );
                """
            )
            expected = {
                "reviewer_id": self.reviewer_id,
                "queue_sha256": sha256_file(self.queue_path),
                "expected_records": str(EXPECTED_RECORDS),
                "review_contract": "exact_privacy_span_exception_review_v1",
            }
            existing = dict(connection.execute("SELECT key,value FROM metadata"))
            if existing:
                drift = [
                    key for key, value in expected.items() if existing.get(key) != value
                ]
                if drift:
                    raise RuntimeError(
                        "片段审核数据库与冻结输入不一致: " + ", ".join(drift)
                    )
            else:
                connection.executemany(
                    "INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?)",
                    [
                        (
                            row["span_review_id"],
                            int(row["review_position"]),
                            int(row["confirmed_title_position"]),
                            row["title_sha256"],
                            row["deidentified_title"],
                            row["sampling_track"],
                            row["queue_sources"],
                            row["prior_review_note"],
                            row["exception_reason"],
                            row["candidate_spans_json"],
                        )
                        for row in rows
                    ],
                )
                for key, value in {**expected, "initialized_at": now()}.items():
                    connection.execute("INSERT INTO metadata VALUES(?,?)", (key, value))
        self._secure_database()

    def _validate_spans(self, title: str, submitted: Any) -> list[dict[str, Any]]:
        if not isinstance(submitted, list):
            raise TypeError("脱敏span必须为列表")
        clean = []
        for item in submitted:
            if not isinstance(item, dict):
                raise TypeError("无效脱敏span")
            start, end = int(item.get("start", -1)), int(item.get("end", -1))
            replacement = str(item.get("replacement", "某人"))
            source = str(item.get("source", "manual"))
            if not 0 <= start < end <= len(title):
                raise ValueError("脱敏span超出标题边界")
            if replacement not in REPLACEMENTS:
                raise ValueError("无效替换类型")
            if source not in {"candidate", "manual"}:
                raise ValueError("无效span来源")
            clean.append(
                {
                    "start": start,
                    "end": end,
                    "text": title[start:end],
                    "replacement": replacement,
                    "source": source,
                }
            )
        clean.sort(key=lambda span: (span["start"], span["end"]))
        for left, right in pairwise(clean):
            if left["end"] > right["start"]:
                raise ValueError("脱敏span不能重叠")
        if len({(span["start"], span["end"]) for span in clean}) != len(clean):
            raise ValueError("脱敏span不能重复")
        return clean

    def save(
        self,
        span_review_id: str,
        action: str,
        spans: Any,
        note: str,
        status: str,
        duration_ms: int,
    ) -> dict[str, Any]:
        action = action.strip()
        note = note.strip()[:2000]
        if status not in {"draft", "reviewed"}:
            raise ValueError("无效审核状态")
        if action and action not in ACTIONS:
            raise ValueError("无效处置动作")
        with self.connect() as connection:
            record = connection.execute(
                "SELECT title FROM records WHERE span_review_id=?", (span_review_id,)
            ).fetchone()
            if not record:
                raise ValueError("记录不在冻结片段审核队列")
            clean_spans = self._validate_spans(record["title"], spans)
            if status == "reviewed" and action not in ACTIONS:
                raise ValueError("请选择处置动作")
            if status == "reviewed" and action == "redact" and not clean_spans:
                raise ValueError("精确脱敏至少选择一个span")
            if status == "reviewed" and action in {"retain", "exclude"} and not note:
                raise ValueError("保留不改或整条排除必须填写理由")
            if action in {"retain", "exclude"} and clean_spans:
                raise ValueError("保留不改或整条排除不能同时提交span")

            timestamp = now()
            old = connection.execute(
                "SELECT * FROM annotations WHERE reviewer_id=? AND span_review_id=?",
                (self.reviewer_id, span_review_id),
            ).fetchone()
            revision = (old["revision"] if old else 0) + 1
            spans_json = json.dumps(
                clean_spans, ensure_ascii=False, separators=(",", ":")
            )
            connection.execute(
                """
                INSERT INTO annotations VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(reviewer_id,span_review_id) DO UPDATE SET
                    action=excluded.action,spans_json=excluded.spans_json,
                    note=excluded.note,status=excluded.status,
                    revision=excluded.revision,updated_at=excluded.updated_at,
                    duration_ms=excluded.duration_ms
                """,
                (
                    self.reviewer_id,
                    span_review_id,
                    action,
                    spans_json,
                    note,
                    status,
                    revision,
                    old["started_at"] if old else timestamp,
                    timestamp,
                    (old["duration_ms"] if old else 0) + max(0, int(duration_ms)),
                ),
            )
            old_json = (
                json.dumps(
                    {
                        "action": old["action"],
                        "spans": json.loads(old["spans_json"]),
                        "note": old["note"],
                    },
                    ensure_ascii=False,
                )
                if old
                else None
            )
            new_json = json.dumps(
                {"action": action, "spans": clean_spans, "note": note},
                ensure_ascii=False,
            )
            connection.execute(
                "INSERT INTO events VALUES(NULL,?,?,?,?,?,?,?)",
                (
                    self.reviewer_id,
                    span_review_id,
                    revision,
                    old_json,
                    new_json,
                    status,
                    timestamp,
                ),
            )
        self._secure_database()
        return {"revision": revision, "status": status}

    def _payload(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if not row:
            return None
        return {
            "span_review_id": row["span_review_id"],
            "position": row["position"],
            "total": EXPECTED_RECORDS,
            "confirmed_title_position": row["confirmed_title_position"],
            "title": row["title"],
            "sampling_track": row["sampling_track"],
            "queue_sources": row["queue_sources"],
            "prior_note": row["prior_note"],
            "exception_reason": row["exception_reason"],
            "candidate_spans": json.loads(row["candidate_spans_json"]),
            "action": row["action"] or "",
            "spans": json.loads(row["spans_json"]) if row["spans_json"] else [],
            "note": row["note"] or "",
            "status": row["status"] or "unreviewed",
            "revision": row["revision"] or 0,
        }

    def record(
        self,
        position: int = 1,
        *,
        next_unreviewed: bool = False,
        after_position: int = 0,
    ) -> dict[str, Any] | None:
        select = """
            SELECT r.*,a.action,a.spans_json,a.note,a.status,a.revision
            FROM records r LEFT JOIN annotations a
              ON a.span_review_id=r.span_review_id AND a.reviewer_id=?
        """
        with self.connect() as connection:
            if next_unreviewed:
                row = connection.execute(
                    select
                    + " WHERE COALESCE(a.status,'unreviewed')!='reviewed' AND r.position>? ORDER BY r.position LIMIT 1",
                    (self.reviewer_id, max(0, after_position)),
                ).fetchone()
                if not row:
                    row = connection.execute(
                        select
                        + " WHERE COALESCE(a.status,'unreviewed')!='reviewed' ORDER BY r.position LIMIT 1",
                        (self.reviewer_id,),
                    ).fetchone()
            else:
                if not 1 <= position <= EXPECTED_RECORDS:
                    raise ValueError("边界复核位置必须在1到9之间")
                row = connection.execute(
                    select + " WHERE r.position=?", (self.reviewer_id, position)
                ).fetchone()
        self._secure_database()
        return self._payload(row)

    def summary(self) -> dict[str, Any]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT a.status,a.action FROM records r LEFT JOIN annotations a
                  ON a.span_review_id=r.span_review_id AND a.reviewer_id=?
                """,
                (self.reviewer_id,),
            ).fetchall()
        return {
            "total": len(rows),
            "status_counts": dict(
                Counter(row["status"] or "unreviewed" for row in rows)
            ),
            "action_counts": dict(
                Counter(row["action"] for row in rows if row["status"] == "reviewed")
            ),
        }

    def metadata(self) -> dict[str, Any]:
        counts = json.loads(self.manifest_path.read_text(encoding="utf-8"))["counts"]
        return {
            "app": "gendered_visibility_privacy_span_review_v1",
            "title": "隐私边界项精确复核台",
            "reviewer_id": self.reviewer_id,
            "summary": self.summary(),
            "note_derived_titles": counts["note_derived_titles"],
            "replacements": [
                {"id": "某人", "label": "某人（姓名／艺名／个人代号）"},
                {"id": "某账号", "label": "某账号"},
                {"id": "某机构", "label": "某机构"},
                {"id": "某地", "label": "某地"},
                {"id": "", "label": "直接删除"},
            ],
        }

    def export(self) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_dir = self.export_root / self.reviewer_id / stamp
        output_dir.mkdir(parents=True, exist_ok=False)
        os.chmod(output_dir, 0o700)
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT r.*,a.action,a.spans_json,a.note,a.status,a.revision,a.updated_at
                FROM records r LEFT JOIN annotations a
                  ON a.span_review_id=r.span_review_id AND a.reviewer_id=?
                ORDER BY r.position
                """,
                (self.reviewer_id,),
            ).fetchall()
        output_rows = [dict(row) for row in rows]
        csv_path = output_dir / "exception_span_adjudications.csv"
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=list(output_rows[0]), lineterminator="\n"
            )
            writer.writeheader()
            writer.writerows(output_rows)
        secure_file(csv_path)
        summary = self.summary()
        manifest = {
            "generated_at": now(),
            "reviewer_id": self.reviewer_id,
            "complete": summary["status_counts"].get("reviewed", 0) == EXPECTED_RECORDS,
            "summary": summary,
            "queue_sha256": sha256_file(self.queue_path),
            "outputs": {csv_path.name: sha256_file(csv_path)},
        }
        manifest_path = output_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        secure_file(manifest_path)
        return output_dir
