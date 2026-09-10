"""SQLite persistence for the local residual-privacy human-review desk."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data/interim/74_gendered_visibility_residual_privacy_human_review_v1"
QUEUE = DATA / "private/review_queue.csv"
MANIFEST = DATA / "manifest.json"
AUDIT_PRIVATE = (
    ROOT / "data/processed/gendered_visibility_residual_privacy_audit_v2/private"
)
PROBABILITY_QUEUE = AUDIT_PRIVATE / "probability_review_sample.csv"
LATIN_QUEUE = AUDIT_PRIVATE / "latin_handle_review_queue.csv"
DECISIONS = {"false_positive", "confirmed_identifier", "unclear"}
EXPECTED_RECORDS = 857


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


class PrivacyReviewStore:
    def __init__(
        self,
        reviewer_id: str,
        db_path: Path,
        queue_path: Path = QUEUE,
        manifest_path: Path = MANIFEST,
        probability_path: Path = PROBABILITY_QUEUE,
        latin_path: Path = LATIN_QUEUE,
        export_root: Path | None = None,
    ) -> None:
        if (
            not reviewer_id
            or len(reviewer_id) > 64
            or not all(
                character.isalnum() or character in "_-" for character in reviewer_id
            )
        ):
            raise ValueError("reviewer id仅可包含字母、数字、_、-")
        self.reviewer_id = reviewer_id
        self.db_path = Path(db_path)
        self.queue_path = Path(queue_path)
        self.manifest_path = Path(manifest_path)
        self.probability_path = Path(probability_path)
        self.latin_path = Path(latin_path)
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
        for source_key, expected_hash in manifest.get("inputs", {}).items():
            source_path = Path(source_key)
            if not source_path.is_absolute():
                source_path = ROOT / source_path
            if not source_path.is_file() or sha256_file(source_path) != expected_hash:
                raise RuntimeError(f"审核台冻结输入SHA-256不一致: {source_key}")
        try:
            queue_key = self.queue_path.resolve().relative_to(ROOT.resolve()).as_posix()
        except ValueError:
            queue_key = self.queue_path.resolve().as_posix()
        expected_queue_hash = manifest.get("outputs", {}).get(queue_key)
        if expected_queue_hash != sha256_file(self.queue_path):
            raise RuntimeError("审核台队列SHA-256与manifest不一致")
        rows = read_csv(self.queue_path)
        required = {
            "review_id",
            "review_position",
            "title_sha256",
            "deidentified_title",
            "queue_sources",
            "latin_matches_json",
            "latin_contexts_json",
        }
        if len(rows) != EXPECTED_RECORDS or not rows or not required.issubset(rows[0]):
            raise RuntimeError("审核台队列不是冻结的857个唯一标题")
        if len({row["review_id"] for row in rows}) != EXPECTED_RECORDS:
            raise RuntimeError("审核台review_id不唯一")
        if [int(row["review_position"]) for row in rows] != list(
            range(1, EXPECTED_RECORDS + 1)
        ):
            raise RuntimeError("审核台位置不是连续的1..857")

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.db_path.parent, 0o700)
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata(
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS records(
                    review_id TEXT PRIMARY KEY,
                    position INTEGER UNIQUE NOT NULL,
                    title_sha256 TEXT UNIQUE NOT NULL,
                    title TEXT NOT NULL,
                    corpus_index INTEGER NOT NULL,
                    sampling_track TEXT NOT NULL,
                    in_probability INTEGER NOT NULL,
                    in_latin INTEGER NOT NULL,
                    probability_review_index INTEGER,
                    latin_review_indices_json TEXT NOT NULL,
                    latin_candidate_ids_json TEXT NOT NULL,
                    latin_matches_json TEXT NOT NULL,
                    latin_contexts_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS annotations(
                    reviewer_id TEXT NOT NULL,
                    review_id TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    note TEXT NOT NULL,
                    status TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL,
                    PRIMARY KEY(reviewer_id, review_id)
                );
                CREATE TABLE IF NOT EXISTS events(
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reviewer_id TEXT NOT NULL,
                    review_id TEXT NOT NULL,
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
                "review_contract": "residual_privacy_title_review_v1",
            }
            existing = dict(connection.execute("SELECT key,value FROM metadata"))
            if existing:
                drift = [
                    key for key, value in expected.items() if existing.get(key) != value
                ]
                if drift:
                    raise RuntimeError(
                        "审核数据库与冻结输入不一致: " + ", ".join(drift)
                    )
            else:
                connection.executemany(
                    "INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [
                        (
                            row["review_id"],
                            int(row["review_position"]),
                            row["title_sha256"],
                            row["deidentified_title"],
                            int(row["corpus_index"]),
                            row["sampling_track"],
                            int("probability" in row["queue_sources"]),
                            int("latin" in row["queue_sources"]),
                            int(row["probability_review_index"])
                            if row["probability_review_index"]
                            else None,
                            row["latin_review_indices_json"],
                            row["latin_candidate_ids_json"],
                            row["latin_matches_json"],
                            row["latin_contexts_json"],
                        )
                        for row in rows
                    ],
                )
                for key, value in {**expected, "initialized_at": now()}.items():
                    connection.execute("INSERT INTO metadata VALUES(?,?)", (key, value))
        self._secure_database()

    def _record_payload(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if not row:
            return None
        return {
            "review_id": row["review_id"],
            "position": row["position"],
            "total": EXPECTED_RECORDS,
            "title": row["title"],
            "sampling_track": row["sampling_track"],
            "in_probability": bool(row["in_probability"]),
            "in_latin": bool(row["in_latin"]),
            "probability_review_index": row["probability_review_index"],
            "latin_review_indices": json.loads(row["latin_review_indices_json"]),
            "latin_matches": json.loads(row["latin_matches_json"]),
            "latin_contexts": json.loads(row["latin_contexts_json"]),
            "decision": row["decision"] or "",
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
            SELECT r.*,a.decision,a.note,a.status,a.revision
            FROM records r
            LEFT JOIN annotations a
              ON a.review_id=r.review_id AND a.reviewer_id=?
        """
        with self.connect() as connection:
            if next_unreviewed:
                row = connection.execute(
                    select
                    + """
                    WHERE COALESCE(a.status,'unreviewed')!='reviewed'
                      AND r.position>?
                    ORDER BY r.position LIMIT 1
                    """,
                    (self.reviewer_id, max(0, after_position)),
                ).fetchone()
                if not row:
                    row = connection.execute(
                        select
                        + """
                        WHERE COALESCE(a.status,'unreviewed')!='reviewed'
                        ORDER BY r.position LIMIT 1
                        """,
                        (self.reviewer_id,),
                    ).fetchone()
            else:
                if not 1 <= position <= EXPECTED_RECORDS:
                    raise ValueError("审核位置必须在1到857之间")
                row = connection.execute(
                    select + " WHERE r.position=?",
                    (self.reviewer_id, position),
                ).fetchone()
        self._secure_database()
        return self._record_payload(row)

    def save(
        self,
        review_id: str,
        decision: str,
        note: str,
        status: str,
        duration_ms: int,
    ) -> dict[str, Any]:
        decision = decision.strip()
        note = note.strip()[:2000]
        if status not in {"draft", "reviewed"}:
            raise ValueError("无效审核状态")
        if decision and decision not in DECISIONS:
            raise ValueError("无效审核结论")
        if status == "reviewed" and not decision:
            raise ValueError("请选择审核结论")
        if status == "reviewed" and decision != "false_positive" and not note:
            raise ValueError("有敏感信息或不确定时必须填写简短理由")

        timestamp = now()
        with self.connect() as connection:
            if not connection.execute(
                "SELECT 1 FROM records WHERE review_id=?", (review_id,)
            ).fetchone():
                raise ValueError("记录不在冻结审核队列")
            old = connection.execute(
                "SELECT * FROM annotations WHERE reviewer_id=? AND review_id=?",
                (self.reviewer_id, review_id),
            ).fetchone()
            revision = (old["revision"] if old else 0) + 1
            connection.execute(
                """
                INSERT INTO annotations VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(reviewer_id,review_id) DO UPDATE SET
                    decision=excluded.decision,
                    note=excluded.note,
                    status=excluded.status,
                    revision=excluded.revision,
                    updated_at=excluded.updated_at,
                    duration_ms=excluded.duration_ms
                """,
                (
                    self.reviewer_id,
                    review_id,
                    decision,
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
                    {"decision": old["decision"], "note": old["note"]},
                    ensure_ascii=False,
                )
                if old
                else None
            )
            new_json = json.dumps(
                {"decision": decision, "note": note}, ensure_ascii=False
            )
            connection.execute(
                "INSERT INTO events VALUES(NULL,?,?,?,?,?,?,?)",
                (
                    self.reviewer_id,
                    review_id,
                    revision,
                    old_json,
                    new_json,
                    status,
                    timestamp,
                ),
            )
        self._secure_database()
        return {"revision": revision, "status": status}

    def summary(self) -> dict[str, Any]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT r.in_probability,r.in_latin,a.status,a.decision
                FROM records r
                LEFT JOIN annotations a
                  ON a.review_id=r.review_id AND a.reviewer_id=?
                ORDER BY r.position
                """,
                (self.reviewer_id,),
            ).fetchall()
        status_counts = Counter(row["status"] or "unreviewed" for row in rows)
        decision_counts = Counter(
            row["decision"] for row in rows if row["status"] == "reviewed"
        )
        probability_rows = [row for row in rows if row["in_probability"]]
        latin_rows = [row for row in rows if row["in_latin"]]
        return {
            "total": len(rows),
            "status_counts": dict(status_counts),
            "decision_counts": dict(decision_counts),
            "queues": {
                "probability": {
                    "total": len(probability_rows),
                    "reviewed": sum(
                        row["status"] == "reviewed" for row in probability_rows
                    ),
                },
                "latin": {
                    "total_unique_titles": len(latin_rows),
                    "candidate_rows": 367,
                    "reviewed_unique_titles": sum(
                        row["status"] == "reviewed" for row in latin_rows
                    ),
                },
            },
        }

    def metadata(self) -> dict[str, Any]:
        return {
            "app": "gendered_visibility_residual_privacy_human_review_v1",
            "title": "残余隐私人工审核台",
            "reviewer_id": self.reviewer_id,
            "summary": self.summary(),
            "decision_codes": {
                "false_positive": "无敏感信息",
                "confirmed_identifier": "有敏感信息",
                "unclear": "不确定",
            },
            "instructions": [
                "只根据当前标题判断，不做反向搜索，不点击链接，不联系任何人。",
                "有敏感信息：仍含可识别或可直接定位的现实个人、账号、联系值、精确地址，或组合后足以定位个人的信息。",
                "无敏感信息：候选只是普通词、虚构/作品语境、占位符、编号或其他误报。",
                "不确定：仅凭标题无法可靠排除现实身份或账号；必须写明疑点。",
            ],
        }

    def _annotations_by_hash(self) -> dict[str, sqlite3.Row]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT r.title_sha256,a.*
                FROM records r
                LEFT JOIN annotations a
                  ON a.review_id=r.review_id AND a.reviewer_id=?
                """,
                (self.reviewer_id,),
            ).fetchall()
        return {row["title_sha256"]: row for row in rows}

    def export(self) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_dir = self.export_root / self.reviewer_id / stamp
        output_dir.mkdir(parents=True, exist_ok=False)
        os.chmod(output_dir, 0o700)
        annotations = self._annotations_by_hash()

        def adjudicated_rows(path: Path) -> list[dict[str, Any]]:
            output = []
            for source in read_csv(path):
                annotation = annotations[source["title_sha256"]]
                output.append(
                    {
                        **source,
                        "review_decision": annotation["decision"] or "",
                        "review_note": annotation["note"] or "",
                        "review_status": annotation["status"] or "unreviewed",
                        "review_revision": annotation["revision"] or 0,
                        "reviewed_at": annotation["updated_at"] or "",
                        "reviewer_id": self.reviewer_id,
                    }
                )
            return output

        output_specs = (
            (
                "probability_review_adjudicated.csv",
                adjudicated_rows(self.probability_path),
            ),
            ("latin_handle_review_adjudicated.csv", adjudicated_rows(self.latin_path)),
        )
        hashes: dict[str, str] = {}
        for filename, rows in output_specs:
            path = output_dir / filename
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=list(rows[0]), lineterminator="\n"
                )
                writer.writeheader()
                writer.writerows(rows)
            secure_file(path)
            hashes[filename] = sha256_file(path)

        summary = self.summary()
        summary_path = output_dir / "summary.json"
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        secure_file(summary_path)
        hashes[summary_path.name] = sha256_file(summary_path)
        export_manifest = {
            "generated_at": now(),
            "reviewer_id": self.reviewer_id,
            "queue_sha256": sha256_file(self.queue_path),
            "complete": summary["status_counts"].get("reviewed", 0) == EXPECTED_RECORDS,
            "summary": summary,
            "outputs": hashes,
        }
        manifest_path = output_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(export_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        secure_file(manifest_path)
        return output_dir
