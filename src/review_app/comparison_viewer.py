"""Local AI/human comparison with a separate, provenance-preserving correction layer."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import sqlite3
import webbrowser
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from src.review_app.reference_viewer import (
    CODEBOOK,
    MANIFEST,
    QUEUE,
    REFERENCE,
    ReferenceDataset,
    sha256,
)
from src.review_app.store import ReviewStore

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data/interim/72_gendered_visibility_human_review_400_v1"
REVIEWER_DB = DATA / "coders/review_reviewer_1.sqlite3"
CORRECTION_DB = DATA / "corrections/author_correction_v1.sqlite3"
STATIC = Path(__file__).with_name("comparison_static")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def payload_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


class CorrectionStore:
    """Freeze reviewer-1 as a baseline and append author corrections separately."""

    def __init__(
        self,
        path: Path,
        reviewer_id: str,
        validator: ReviewStore,
        frozen_inputs: dict[str, str],
    ) -> None:
        self.path = Path(path)
        self.reviewer_id = reviewer_id
        self.validator = validator
        self.frozen_inputs = frozen_inputs
        self.semantic_ids = [
            field_id
            for field_id in validator.fields
            if field_id not in {"needs_adjudication", "evidence_notes"}
        ]

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def initialize(self, original: dict[str, dict[str, Any]]) -> None:
        if len(original) != 400:
            raise ValueError("作者校正基线必须严格包含400条人工1结果")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        baseline_hash = payload_hash(original)
        expected = {
            "schema_version": "1",
            "source_reviewer_id": self.reviewer_id,
            "source_labels_sha256": baseline_hash,
            **self.frozen_inputs,
        }
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata(
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS baseline(
                    blind_id TEXT PRIMARY KEY,
                    annotation_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS corrections(
                    blind_id TEXT PRIMARY KEY,
                    corrected_json TEXT,
                    reason TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    active INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events(
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    blind_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    old_json TEXT,
                    new_json TEXT,
                    reason TEXT NOT NULL,
                    event_time TEXT NOT NULL
                );
                """
            )
            existing = dict(connection.execute("SELECT key,value FROM metadata"))
            if existing:
                mismatched = [
                    key for key, value in expected.items() if existing.get(key) != value
                ]
                if mismatched:
                    raise RuntimeError(
                        "作者校正库与原始人工基线或冻结输入不一致: "
                        + ", ".join(mismatched)
                    )
                if connection.execute("SELECT COUNT(*) FROM baseline").fetchone()[0] != 400:
                    raise RuntimeError("作者校正库的人工基线不完整")
                return
            connection.executemany(
                "INSERT INTO baseline VALUES(?,?)",
                [
                    (
                        blind_id,
                        json.dumps(annotation, ensure_ascii=False, sort_keys=True),
                    )
                    for blind_id, annotation in sorted(original.items())
                ],
            )
            for key, value in {**expected, "initialized_at": now()}.items():
                connection.execute("INSERT INTO metadata VALUES(?,?)", (key, value))

    def baseline(self) -> dict[str, dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT blind_id,annotation_json FROM baseline"
            ).fetchall()
        return {row["blind_id"]: json.loads(row["annotation_json"]) for row in rows}

    def state(self, blind_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM corrections WHERE blind_id=?", (blind_id,)
            ).fetchone()
        if not row:
            return None
        return {
            "active": bool(row["active"]),
            "annotation": json.loads(row["corrected_json"])
            if row["active"]
            else None,
            "reason": row["reason"],
            "revision": row["revision"],
            "updated_at": row["updated_at"],
        }

    def current(self, blind_id: str) -> dict[str, Any] | None:
        state = self.state(blind_id)
        return state if state and state["active"] else None

    def count(self) -> int:
        with self.connect() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM corrections WHERE active=1"
                ).fetchone()[0]
            )

    def _validated_payload(
        self, annotation: dict[str, Any], reason: str
    ) -> dict[str, Any]:
        if set(annotation) != set(self.semantic_ids):
            raise ValueError("作者校正必须提交完整的16个语义字段")
        clean_reason = str(reason).strip()
        if not clean_reason:
            raise ValueError("修改人工结果时必须填写校正理由")
        payload = dict(annotation)
        payload["needs_adjudication"] = (
            "yes" if any(value == "unclear" for value in annotation.values()) else "no"
        )
        payload["evidence_notes"] = clean_reason
        return self.validator.validate(payload, "reviewed")

    def save(
        self,
        blind_id: str,
        annotation: dict[str, Any],
        reason: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        clean = self._validated_payload(annotation, reason)
        baseline = self.baseline().get(blind_id)
        if not baseline:
            raise ValueError("记录不在冻结人工基线中")
        if all(clean[field_id] == baseline[field_id] for field_id in self.semantic_ids):
            raise ValueError("校正结果与原始人工1完全相同，无需保存")
        timestamp = now()
        encoded = json.dumps(clean, ensure_ascii=False, sort_keys=True)
        with self.connect() as connection:
            old = connection.execute(
                "SELECT * FROM corrections WHERE blind_id=?", (blind_id,)
            ).fetchone()
            revision = int(old["revision"] if old else 0)
            if revision != int(expected_revision):
                raise RuntimeError("校正已在其他页面更新，请刷新后重试")
            new_revision = revision + 1
            connection.execute(
                """
                INSERT INTO corrections VALUES(?,?,?,?,?,?)
                ON CONFLICT(blind_id) DO UPDATE SET
                    corrected_json=excluded.corrected_json,
                    reason=excluded.reason,
                    revision=excluded.revision,
                    active=1,
                    updated_at=excluded.updated_at
                """,
                (blind_id, encoded, reason.strip(), new_revision, 1, timestamp),
            )
            connection.execute(
                "INSERT INTO events(blind_id,revision,action,old_json,new_json,reason,event_time) VALUES(?,?,?,?,?,?,?)",
                (
                    blind_id,
                    new_revision,
                    "save",
                    old["corrected_json"] if old and old["active"] else None,
                    encoded,
                    reason.strip(),
                    timestamp,
                ),
            )
        return {"revision": new_revision, "updated_at": timestamp}

    def reset(self, blind_id: str, expected_revision: int) -> dict[str, Any]:
        timestamp = now()
        with self.connect() as connection:
            old = connection.execute(
                "SELECT * FROM corrections WHERE blind_id=?", (blind_id,)
            ).fetchone()
            if not old or not old["active"]:
                raise ValueError("本条没有可撤销的作者校正")
            if int(old["revision"]) != int(expected_revision):
                raise RuntimeError("校正已在其他页面更新，请刷新后重试")
            new_revision = int(old["revision"]) + 1
            connection.execute(
                "UPDATE corrections SET active=0,revision=?,reason=?,updated_at=? WHERE blind_id=?",
                (new_revision, "撤销作者校正，恢复原始人工1基线", timestamp, blind_id),
            )
            connection.execute(
                "INSERT INTO events(blind_id,revision,action,old_json,new_json,reason,event_time) VALUES(?,?,?,?,?,?,?)",
                (
                    blind_id,
                    new_revision,
                    "reset",
                    old["corrected_json"],
                    None,
                    "撤销作者校正，恢复原始人工1基线",
                    timestamp,
                ),
            )
        return {"revision": new_revision, "updated_at": timestamp}


class ComparisonDataset:
    """Join AI and frozen human baseline, with corrections stored separately."""

    def __init__(
        self,
        reviewer_db: Path = REVIEWER_DB,
        reviewer_id: str = "reviewer_1",
        correction_db: Path = CORRECTION_DB,
        queue_path: Path = QUEUE,
        reference_path: Path = REFERENCE,
        codebook_path: Path = CODEBOOK,
        manifest_path: Path = MANIFEST,
    ) -> None:
        self.reviewer_db = Path(reviewer_db)
        self.reviewer_id = reviewer_id
        self.codebook_path = Path(codebook_path)
        self.queue_path = Path(queue_path)
        reference = ReferenceDataset(
            queue_path, reference_path, codebook_path, manifest_path
        )
        self.fields = reference.fields
        self.validator = ReviewStore(
            "author_correction_v1",
            Path(correction_db),
            Path(queue_path),
            Path(codebook_path),
        )
        self._option_labels = {
            field["id"]: {
                option["id"]: option["label"] for option in field.get("options", [])
            }
            for field in self.fields
        }
        live_human = self._load_human_labels()
        self.corrections = CorrectionStore(
            Path(correction_db),
            reviewer_id,
            self.validator,
            {
                "queue_sha256": sha256(Path(queue_path)),
                "reference_sha256": sha256(Path(reference_path)),
                "codebook_sha256": sha256(Path(codebook_path)),
                "manifest_sha256": sha256(Path(manifest_path)),
            },
        )
        self.corrections.initialize(live_human)
        human = self.corrections.baseline()

        records: list[dict[str, Any]] = []
        for source in reference.records:
            blind_id = source["blind_id"]
            if blind_id not in human:
                raise ValueError(f"人工数据库缺少已完成记录: {blind_id}")
            annotation = human[blind_id]
            field_rows = []
            for field in self.fields:
                field_id = field["id"]
                ai_value = source["reference_output"][field_id]
                human_value = annotation[field_id]
                field_rows.append(
                    {
                        "id": field_id,
                        "ai": ai_value,
                        "ai_label": self._label(field_id, ai_value),
                        "human": human_value,
                        "human_label": self._label(field_id, human_value),
                        "same": ai_value == human_value,
                    }
                )
            records.append(
                {
                    "position": source["position"],
                    "blind_id": blind_id,
                    "title": source["title"],
                    "fields": field_rows,
                    "difference_count": sum(not row["same"] for row in field_rows),
                    "needs_adjudication": annotation.get("needs_adjudication", "no"),
                    "evidence_notes": annotation.get("evidence_notes", ""),
                }
            )
        if len(records) != 400:
            raise ValueError("AI—人工比较必须严格连接400条记录")
        self.records = records
        self.different_positions = [
            record["position"]
            for record in records
            if record["difference_count"] > 0
        ]

    def _label(self, field_id: str, value: str) -> str:
        try:
            return self._option_labels[field_id][value]
        except KeyError as error:
            raise ValueError(f"人工数据库存在无效代码: {field_id}={value}") from error

    def _load_human_labels(self) -> dict[str, dict[str, Any]]:
        if not self.reviewer_db.is_file():
            raise FileNotFoundError(f"找不到人工审核数据库: {self.reviewer_db}")
        uri = f"file:{self.reviewer_db.resolve()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            rows = connection.execute(
                """
                SELECT r.blind_id, a.annotation_json, a.status
                FROM annotations AS a
                JOIN records AS r ON r.record_id = a.record_id
                WHERE a.coder_id = ?
                """,
                (self.reviewer_id,),
            ).fetchall()
        if len(rows) != 400 or Counter(row[2] for row in rows) != {"reviewed": 400}:
            raise ValueError("比较页要求指定审核员已经完成400/400条")
        labels = {blind_id: json.loads(payload) for blind_id, payload, _ in rows}
        if len(labels) != 400:
            raise ValueError("人工审核数据库的盲ID不唯一")
        return labels

    def metadata(self) -> dict[str, Any]:
        total_cells = len(self.records) * len(self.fields)
        same_cells = sum(
            row["same"] for record in self.records for row in record["fields"]
        )
        field_summary = []
        for field in self.fields:
            field_id = field["id"]
            same = sum(
                next(row for row in record["fields"] if row["id"] == field_id)[
                    "same"
                ]
                for record in self.records
            )
            field_summary.append(
                {
                    "id": field_id,
                    "label": field["label"],
                    "same": same,
                    "different": len(self.records) - same,
                    "agreement": same / len(self.records),
                }
            )
        return {
            "app": "gendered_visibility_ai_human_comparison_viewer",
            "title": "作者专用：AI标注与人工1差异与校正台",
            "total": len(self.records),
            "fields": self.fields,
            "field_summary": field_summary,
            "different_records": len(self.different_positions),
            "different_positions": self.different_positions,
            "cell_agreement": same_cells / total_cells,
            "reviewer_id": self.reviewer_id,
            "correction_records": self.corrections.count(),
            "warning": "原始人工1保持只读；作者校正另存并记录理由与历史。AI和人工都不是自动金标准。",
            "read_only": False,
            "original_human_read_only": True,
        }

    def record(self, position: int) -> dict[str, Any]:
        if not 1 <= position <= len(self.records):
            raise ValueError(f"位置必须在1到{len(self.records)}之间")
        record = deepcopy(self.records[position - 1])
        correction_state = self.corrections.state(record["blind_id"])
        correction = (
            correction_state
            if correction_state and correction_state["active"]
            else None
        )
        corrected = correction["annotation"] if correction else {
            row["id"]: row["human"] for row in record["fields"]
        }
        for row in record["fields"]:
            value = corrected[row["id"]]
            row["corrected"] = value
            row["corrected_label"] = self._label(row["id"], value)
            row["human_changed"] = value != row["human"]
            row["corrected_matches_ai"] = value == row["ai"]
        record["correction_count"] = sum(
            row["human_changed"] for row in record["fields"]
        )
        record["corrected_difference_count"] = sum(
            not row["corrected_matches_ai"] for row in record["fields"]
        )
        record["correction_reason"] = correction["reason"] if correction else ""
        record["correction_revision"] = (
            correction_state["revision"] if correction_state else 0
        )
        record["correction_updated_at"] = (
            correction["updated_at"] if correction else None
        )
        return record

    def save_correction(
        self,
        position: int,
        annotation: dict[str, Any],
        reason: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        record = self.record(position)
        self.corrections.save(
            record["blind_id"], annotation, reason, expected_revision
        )
        return self.record(position)

    def reset_correction(self, position: int, expected_revision: int) -> dict[str, Any]:
        record = self.record(position)
        self.corrections.reset(record["blind_id"], expected_revision)
        return self.record(position)


class Handler(BaseHTTPRequestHandler):
    dataset: ComparisonDataset

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def send_headers(self, status: int, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'",
        )
        self.end_headers()

    def send_json(self, value: object, status: int = 200) -> None:
        self.send_headers(status, "application/json; charset=utf-8")
        self.wfile.write(json.dumps(value, ensure_ascii=False).encode())

    def local_request(self) -> bool:
        return self.headers.get("Host", "").split(":")[0] in {
            "127.0.0.1",
            "localhost",
        }

    def do_GET(self) -> None:
        if not self.local_request():
            self.send_json({"error": "local access only"}, 403)
            return
        try:
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            if parsed.path == "/api/health":
                self.send_json(
                    {
                        "ok": True,
                        "original_human_read_only": True,
                        "correction_layer_writable": True,
                    }
                )
                return
            if parsed.path == "/api/meta":
                self.send_json(self.dataset.metadata())
                return
            if parsed.path == "/api/comparison":
                position = int(query.get("position", [1])[0])
                self.send_json({"record": self.dataset.record(position)})
                return
            relative_path = "index.html" if parsed.path == "/" else parsed.path.lstrip("/")
            target = (STATIC / relative_path).resolve()
            if STATIC.resolve() not in target.parents or not target.is_file():
                self.send_json({"error": "not found"}, 404)
                return
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if content_type.startswith("text/") or content_type == "application/javascript":
                content_type += "; charset=utf-8"
            self.send_headers(200, content_type)
            self.wfile.write(target.read_bytes())
        except Exception as error:  # noqa: BLE001
            self.send_json({"error": str(error)}, 400)

    def do_POST(self) -> None:
        if not self.local_request():
            self.send_json({"error": "local access only"}, 403)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1_000_000:
                raise ValueError("无效请求大小")
            payload = json.loads(self.rfile.read(length))
            if self.path == "/api/correction":
                record = self.dataset.save_correction(
                    int(payload["position"]),
                    payload["annotation"],
                    str(payload.get("reason", "")),
                    int(payload.get("expected_revision", 0)),
                )
                self.send_json({"record": record})
                return
            if self.path == "/api/correction/reset":
                record = self.dataset.reset_correction(
                    int(payload["position"]),
                    int(payload.get("expected_revision", 0)),
                )
                self.send_json({"record": record})
                return
            self.send_json({"error": "not found"}, 404)
        except Exception as error:  # noqa: BLE001
            self.send_json({"error": str(error)}, 400)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8768)
    parser.add_argument("--reviewer-db", type=Path, default=REVIEWER_DB)
    parser.add_argument("--reviewer-id", default="reviewer_1")
    parser.add_argument("--correction-db", type=Path, default=CORRECTION_DB)
    parser.add_argument("--queue", type=Path, default=QUEUE)
    parser.add_argument("--reference", type=Path, default=REFERENCE)
    parser.add_argument("--codebook", type=Path, default=CODEBOOK)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()

    Handler.dataset = ComparisonDataset(
        args.reviewer_db,
        args.reviewer_id,
        args.correction_db,
        args.queue,
        args.reference,
        args.codebook,
        args.manifest,
    )
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"AI-human comparison and correction viewer (author-only): {url}")
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
