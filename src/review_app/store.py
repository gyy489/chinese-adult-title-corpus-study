"""Per-reviewer SQLite persistence for local blind-review tasks."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data/interim/45_human_blind_review_v1"
QUEUE = DATA / "private/blind_queue.csv"
CODEBOOK = ROOT / "config/human_blind_review_codebook_v1.json"
DEFAULT_SOURCE_CODEBOOK = ROOT / "config/ai_coding_codebook_v1_candidate.json"
CODER_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def data_dir_for_queue(queue_path: Path) -> Path:
    if queue_path.parent.name == "private":
        return queue_path.parent.parent
    return queue_path.parent


class ReviewStore:
    def __init__(
        self,
        coder_id: str,
        db_path: Path,
        queue_path: Path = QUEUE,
        codebook_path: Path = CODEBOOK,
    ) -> None:
        if not CODER_RE.fullmatch(coder_id):
            raise ValueError("coder id仅可包含字母、数字、_、-")
        self.coder_id = coder_id
        self.db_path = Path(db_path)
        self.queue_path = Path(queue_path)
        self.codebook_path = Path(codebook_path)
        self.data_dir = data_dir_for_queue(self.queue_path)
        raw_codebook = json.loads(self.codebook_path.read_text(encoding="utf-8"))
        self.base_codebook_path: Path | None = None
        if raw_codebook.get("extends"):
            self.base_codebook_path = (
                ROOT / str(raw_codebook["extends"])
            ).resolve()
            if ROOT.resolve() not in self.base_codebook_path.parents:
                raise ValueError("扩展代码本必须位于项目目录内")
            base = json.loads(self.base_codebook_path.read_text(encoding="utf-8"))
            self.codebook = deepcopy(base)
            for key, value in raw_codebook.items():
                if key not in {"extends", "field_overrides"}:
                    self.codebook[key] = deepcopy(value)
            fields = {field["id"]: field for field in self.codebook["fields"]}
            for field_id, override in raw_codebook.get("field_overrides", {}).items():
                if field_id not in fields:
                    raise ValueError(f"扩展代码本包含未知字段：{field_id}")
                fields[field_id].update(deepcopy(override))
        else:
            self.codebook = raw_codebook
        self.expected_records = int(self.codebook.get("expected_records", 300))
        self.require_unique_clusters = bool(
            self.codebook.get("require_unique_clusters", True)
        )
        self.require_complete_export = bool(
            self.codebook.get("require_complete_export", False)
        )
        self.review_contract = str(self.codebook.get("review_contract", "legacy_v1"))

        option_sets = self.codebook.get("option_sets", {})
        for field in self.codebook["fields"]:
            if "options" not in field and field.get("option_set"):
                field["options"] = deepcopy(option_sets[field["option_set"]])

        configured_source = self.codebook.get(
            "source_codebook", DEFAULT_SOURCE_CODEBOOK.relative_to(ROOT).as_posix()
        )
        self.source_codebook_path = ROOT / configured_source
        source = json.loads(self.source_codebook_path.read_text(encoding="utf-8"))
        source_fields = {field["id"]: field for field in source.get("fields", [])}
        for field in self.codebook["fields"]:
            source_options = source_fields.get(field["id"], {}).get("options", [])
            definitions = {
                option["id"]: option
                for option in source_options
                if isinstance(option, dict) and "id" in option
            }
            for option in field.get("options", []):
                if option["id"] in definitions:
                    for key in ("rule_id", "definition", "exclude"):
                        if key in definitions[option["id"]] and key not in option:
                            option[key] = definitions[option["id"]][key]

        self.fields = {field["id"]: field for field in self.codebook["fields"]}
        self.options = {
            field_id: {option["id"] for option in field.get("options", [])}
            for field_id, field in self.fields.items()
        }

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def initialize(self) -> None:
        with self.queue_path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        required = {
            "record_id",
            "blind_id",
            "position",
            "deidentified_title",
            "cluster_id",
        }
        if not rows or not required.issubset(rows[0]):
            raise ValueError("盲审队列字段不完整")
        if len(rows) != self.expected_records:
            raise ValueError(f"盲审队列必须包含{self.expected_records}条记录")
        if len({row["record_id"] for row in rows}) != self.expected_records:
            raise ValueError("盲审队列record_id必须唯一")
        if len({row["blind_id"] for row in rows}) != self.expected_records:
            raise ValueError("盲审队列blind_id必须唯一")
        if (
            self.require_unique_clusters
            and len({row["cluster_id"] for row in rows}) != self.expected_records
        ):
            raise ValueError("盲审队列表达簇必须唯一")
        if any(not row["deidentified_title"].strip() for row in rows):
            raise ValueError("盲审队列存在空标题")

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata(
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS records(
                    record_id TEXT PRIMARY KEY,
                    blind_id TEXT UNIQUE,
                    position INTEGER UNIQUE,
                    title TEXT NOT NULL,
                    cluster_id TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS annotations(
                    coder_id TEXT,
                    record_id TEXT,
                    annotation_json TEXT,
                    status TEXT,
                    revision INTEGER,
                    started_at TEXT,
                    updated_at TEXT,
                    duration_ms INTEGER,
                    PRIMARY KEY(coder_id, record_id)
                );
                CREATE TABLE IF NOT EXISTS events(
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    coder_id TEXT,
                    record_id TEXT,
                    revision INTEGER,
                    old_json TEXT,
                    new_json TEXT,
                    status TEXT,
                    event_time TEXT
                );
                """
            )
            expected = {
                "coder_id": self.coder_id,
                "queue_sha256": sha256(self.queue_path),
                "codebook_sha256": sha256(self.codebook_path),
                "source_codebook_sha256": sha256(self.source_codebook_path),
                "codebook_version": str(self.codebook["version"]),
                "review_contract": self.review_contract,
                "expected_records": str(self.expected_records),
            }
            if self.base_codebook_path:
                expected["base_codebook_sha256"] = sha256(self.base_codebook_path)
            existing = dict(connection.execute("SELECT key, value FROM metadata"))
            if existing:
                bad = [
                    key for key, value in expected.items() if existing.get(key) != value
                ]
                if bad:
                    raise RuntimeError("数据库与冻结输入不一致: " + ", ".join(bad))
                return

            order = sorted(
                rows,
                key=lambda row: hashlib.sha256(
                    f"{self.coder_id}|{row['blind_id']}".encode()
                ).hexdigest(),
            )
            connection.executemany(
                "INSERT INTO records VALUES(?,?,?,?,?)",
                [
                    (
                        row["record_id"],
                        row["blind_id"],
                        position,
                        row["deidentified_title"],
                        row["cluster_id"],
                    )
                    for position, row in enumerate(order, start=1)
                ],
            )
            for key, value in {**expected, "initialized_at": now()}.items():
                connection.execute("INSERT INTO metadata VALUES(?,?)", (key, value))

    def metadata(self) -> dict[str, Any]:
        return {
            "app": "human_blind_review",
            "coder_id": self.coder_id,
            "title": self.codebook["title"],
            "version": self.codebook["version"],
            "instructions": self.codebook["instructions"],
            "fields": self.codebook["fields"],
            "summary": self.summary(),
        }

    def record(
        self, position: int | None = None, next_unreviewed: bool = False
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            if next_unreviewed:
                row = connection.execute(
                    """
                    SELECT r.*, a.annotation_json, a.status, a.revision, a.duration_ms
                    FROM records r
                    LEFT JOIN annotations a
                      ON a.record_id=r.record_id AND a.coder_id=?
                    WHERE COALESCE(a.status,'unreviewed')!='reviewed'
                    ORDER BY CASE WHEN a.status='draft' THEN 0 ELSE 1 END, r.position
                    LIMIT 1
                    """,
                    (self.coder_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT r.*, a.annotation_json, a.status, a.revision, a.duration_ms
                    FROM records r
                    LEFT JOIN annotations a
                      ON a.record_id=r.record_id AND a.coder_id=?
                    WHERE r.position=?
                    """,
                    (self.coder_id, position or 1),
                ).fetchone()
        if not row:
            return None
        return {
            "record_id": row["record_id"],
            "blind_id": row["blind_id"],
            "position": row["position"],
            "total": self.expected_records,
            "title": row["title"],
            "annotation": json.loads(row["annotation_json"])
            if row["annotation_json"]
            else {},
            "status": row["status"] or "unreviewed",
            "revision": row["revision"] or 0,
        }

    def search_reviewed(
        self, field_id: str, option_id: str, title_query: str = ""
    ) -> dict[str, Any]:
        field = self.fields.get(field_id)
        if not field or field.get("type") == "text":
            raise ValueError("请选择可按选项查找的标注字段")
        if option_id not in self.options[field_id]:
            raise ValueError("请选择有效的标注选项")
        summary = self.summary()
        reviewed = int(summary["status_counts"].get("reviewed", 0))
        if reviewed != self.expected_records:
            raise ValueError(
                f"完成全部{self.expected_records}条审核后才能按条件查找修改"
            )

        needle = title_query.strip().casefold()
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT r.position, r.blind_id, r.title, a.annotation_json
                FROM records r
                JOIN annotations a
                  ON a.record_id=r.record_id AND a.coder_id=?
                WHERE a.status='reviewed'
                ORDER BY r.position
                """,
                (self.coder_id,),
            ).fetchall()

        results = []
        for row in rows:
            annotation = json.loads(row["annotation_json"])
            selected = annotation.get(field_id)
            matches = (
                option_id in selected if isinstance(selected, list) else selected == option_id
            )
            if matches and (not needle or needle in row["title"].casefold()):
                results.append(
                    {
                        "position": row["position"],
                        "blind_id": row["blind_id"],
                        "title": row["title"],
                    }
                )
        return {
            "field_id": field_id,
            "option_id": option_id,
            "title_query": title_query.strip(),
            "count": len(results),
            "results": results,
        }

    def _option_requires_note(self, field_id: str, value: str) -> bool:
        return any(
            option["id"] == value and bool(option.get("requires_note"))
            for option in self.fields[field_id].get("options", [])
        )

    def _validate_legacy(self, clean: dict[str, Any]) -> None:
        invalid = clean["record_validity"] != "valid"
        if invalid and (
            clean["social_script_codes"] != ["none"]
            or clean["harm_script_codes"] != ["none"]
            or clean["minor_age_reference_status"] != "not_applicable"
            or clean["minor_age_cue_types"] != ["none"]
        ):
            raise ValueError("非有效记录的语义字段应选择无／不适用")
        age = clean["minor_age_reference_status"]
        cues = clean["minor_age_cue_types"]
        if age == "absent" and cues != ["none"]:
            raise ValueError("年龄无可见线索时，线索类型应选无")
        if age == "unknown" and cues != ["unknown"]:
            raise ValueError("年龄相关但不明时，线索类型应选不明")

    def _validate_gendered_visibility(self, clean: dict[str, Any]) -> None:
        metadata_fields = {"record_validity", "needs_adjudication", "evidence_notes"}
        semantic_fields = set(self.fields) - metadata_fields
        if clean["record_validity"] != "valid":
            if any(clean[field] != "not_applicable" for field in semantic_fields):
                raise ValueError("非有效记录的全部论文语义字段都应选择“不适用”")
            return

        person_fields = {
            "objectification_target_position",
            "desire_holder_position",
            "pleasure_holder_position",
            "refusal_resistance_position",
            "attributed_speech_position",
        }
        if any(clean[field] == "not_applicable" for field in person_fields):
            raise ValueError("有效记录的人物位置字段无可见线索时应选择“不可见”")

        privacy_fields = {
            "material_creation_visibility",
            "material_creation_target_position",
            "distribution_visibility",
            "distribution_actor_position",
            "distribution_actor_realization",
            "distribution_target_position",
            "distribution_authorization_visibility",
            "exposure_visibility",
            "exposure_target_position",
        }
        relevance = clean["privacy_chain_relevance"]
        if relevance == "not_relevant":
            if any(clean[field] != "not_applicable" for field in privacy_fields):
                raise ValueError("隐私链不相关时，全部阶段字段应选择“不适用”")
            return
        if relevance == "unclear":
            if any(clean[field] != "unclear" for field in privacy_fields):
                raise ValueError("隐私链相关性不明时，全部阶段字段应选择“不明”")
            return
        if relevance != "relevant":
            raise ValueError("有效记录的隐私链相关性不能为“不适用”")

        stage_pairs = (
            ("material_creation_visibility", "material_creation_target_position"),
            ("exposure_visibility", "exposure_target_position"),
        )
        for visibility_field, target_field in stage_pairs:
            visibility = clean[visibility_field]
            target = clean[target_field]
            if visibility == "not_applicable":
                raise ValueError("隐私链相关时，阶段可见性不能为“不适用”")
            if visibility == "absent" and target != "not_visible":
                raise ValueError("阶段缺席时，对象位置应选择“不可见”")
            if visibility == "unclear" and target != "unclear":
                raise ValueError("阶段不明时，对象位置应选择“不明”")
            if visibility == "visible" and target == "not_applicable":
                raise ValueError("阶段可见时，对象位置不能为“不适用”")

        distribution = clean["distribution_visibility"]
        actor = clean["distribution_actor_position"]
        target = clean["distribution_target_position"]
        realization = clean["distribution_actor_realization"]
        authorization = clean["distribution_authorization_visibility"]
        if distribution == "absent":
            expected = (
                "not_visible",
                "not_visible",
                "not_applicable",
                "not_applicable",
            )
            if (actor, target, realization, authorization) != expected:
                raise ValueError("分发阶段缺席时，位置应不可见，实现与授权应不适用")
            return
        if distribution == "unclear":
            if (actor, target, realization, authorization) != (
                "unclear",
                "unclear",
                "unclear",
                "unclear",
            ):
                raise ValueError("分发阶段不明时，四个条件字段都应选择“不明”")
            return
        if distribution != "visible":
            raise ValueError("隐私链相关时，分发可见性不能为“不适用”")
        if "not_applicable" in {actor, target, realization, authorization}:
            raise ValueError("分发阶段可见时，条件字段不能为“不适用”")

        omitted_realizations = {
            "passive_agent_omitted",
            "nominalized_event_without_agent",
            "no_actor_expression",
        }
        visible_realizations = {
            "explicit_gendered_person_or_role",
            "explicit_ungendered_person_or_role",
            "generic_or_collective_actor",
            "zero_anaphora_coreferential",
        }
        if actor == "not_visible" and realization not in omitted_realizations:
            raise ValueError("分发行动者不可见时，须选择一种行动者省略实现")
        if actor == "unclear" and realization != "unclear":
            raise ValueError("分发行动者位置不明时，实现方式也应不明")
        if (
            actor not in {"not_visible", "unclear"}
            and realization not in visible_realizations
        ):
            raise ValueError("可见的分发行动者须选择显式、集合或零回指实现")

    def validate(self, value: dict[str, Any], status: str) -> dict[str, Any]:
        if status not in {"draft", "reviewed"} or not isinstance(value, dict):
            raise ValueError("无效提交")
        complete = status == "reviewed"
        clean: dict[str, Any] = {}
        note_required = False
        for field_id, field in self.fields.items():
            field_type = field["type"]
            submitted = value.get(field_id, [] if field_type == "multi" else "")
            if field_type == "text":
                clean[field_id] = str(submitted)[: field.get("max_length", 2000)]
                continue
            if field_type == "single":
                if complete and not submitted:
                    raise ValueError("请完成：" + field["label"])
                if submitted and submitted not in self.options[field_id]:
                    raise ValueError("无效选项：" + field_id)
                if submitted and self._option_requires_note(field_id, str(submitted)):
                    note_required = True
            else:
                if not isinstance(submitted, list):
                    raise ValueError(field_id + "必须为列表")
                submitted = list(dict.fromkeys(submitted))
                if complete and not submitted:
                    raise ValueError("请完成：" + field["label"])
                if set(submitted) - self.options[field_id]:
                    raise ValueError("无效选项：" + field_id)
                if (
                    set(submitted) & set(field.get("exclusive_options", []))
                    and len(submitted) > 1
                ):
                    raise ValueError(field["label"] + "中的互斥值不能与其他项并选")
                if any(
                    self._option_requires_note(field_id, str(item))
                    for item in submitted
                ):
                    note_required = True
            clean[field_id] = submitted

        if not complete:
            return clean
        if self.review_contract == "legacy_v1":
            self._validate_legacy(clean)
        elif self.review_contract == "gendered_visibility_paper_core_v1":
            self._validate_gendered_visibility(clean)
        else:
            raise ValueError(f"未知审核契约：{self.review_contract}")

        if any(
            (isinstance(item, str) and item in {"unknown", "other"})
            or (isinstance(item, list) and bool({"unknown", "other"} & set(item)))
            for field_id, item in clean.items()
            if field_id not in {"evidence_notes", "needs_adjudication"}
        ):
            note_required = True
        has_unclear = any(
            item == "unclear" or (isinstance(item, list) and "unclear" in item)
            for field_id, item in clean.items()
            if field_id not in {"evidence_notes", "needs_adjudication"}
        )
        if has_unclear and clean.get("needs_adjudication") != "yes":
            raise ValueError("任一字段选择“不明”时，必须标记“需要裁决”")
        if clean.get("needs_adjudication") == "yes":
            note_required = True
        if note_required and not clean.get("evidence_notes", "").strip():
            raise ValueError("不明、特殊有效性判断或需裁决时必须填写证据与理由")
        return clean

    def save(
        self,
        record_id: str,
        annotation: dict[str, Any],
        status: str,
        duration_ms: int,
    ) -> dict[str, Any]:
        clean = self.validate(annotation, status)
        timestamp = now()
        with self.connect() as connection:
            if not connection.execute(
                "SELECT 1 FROM records WHERE record_id=?", (record_id,)
            ).fetchone():
                raise ValueError("记录不在冻结队列")
            old = connection.execute(
                "SELECT * FROM annotations WHERE coder_id=? AND record_id=?",
                (self.coder_id, record_id),
            ).fetchone()
            revision = (old["revision"] if old else 0) + 1
            payload = json.dumps(clean, ensure_ascii=False, sort_keys=True)
            connection.execute(
                """
                INSERT INTO annotations VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(coder_id,record_id) DO UPDATE SET
                    annotation_json=excluded.annotation_json,
                    status=excluded.status,
                    revision=excluded.revision,
                    updated_at=excluded.updated_at,
                    duration_ms=excluded.duration_ms
                """,
                (
                    self.coder_id,
                    record_id,
                    payload,
                    status,
                    revision,
                    old["started_at"] if old else timestamp,
                    timestamp,
                    (old["duration_ms"] if old else 0) + max(0, int(duration_ms)),
                ),
            )
            connection.execute(
                """
                INSERT INTO events(
                    coder_id,record_id,revision,old_json,new_json,status,event_time
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    self.coder_id,
                    record_id,
                    revision,
                    old["annotation_json"] if old else None,
                    payload,
                    status,
                    timestamp,
                ),
            )
        return {"revision": revision, "status": status}

    def summary(self) -> dict[str, Any]:
        with self.connect() as connection:
            counts = Counter(
                row[0] or "unreviewed"
                for row in connection.execute(
                    """
                    SELECT a.status
                    FROM records r
                    LEFT JOIN annotations a
                      ON a.record_id=r.record_id AND a.coder_id=?
                    """,
                    (self.coder_id,),
                )
            )
        return {"total": self.expected_records, "status_counts": dict(counts)}

    def export(self) -> Path:
        summary = self.summary()
        reviewed = int(summary["status_counts"].get("reviewed", 0))
        if self.require_complete_export and reviewed != self.expected_records:
            raise ValueError(
                f"完成{self.expected_records}条后才能导出；当前已完成{reviewed}条"
            )
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_dir = self.data_dir / "exports" / self.coder_id / stamp
        output_dir.mkdir(parents=True)
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT r.blind_id,r.position,r.record_id,r.cluster_id,a.*
                FROM annotations a
                JOIN records r ON r.record_id=a.record_id
                WHERE a.coder_id=?
                ORDER BY r.position
                """,
                (self.coder_id,),
            ).fetchall()
        annotations_path = output_dir / "annotations.jsonl"
        annotations_path.write_text(
            "".join(
                json.dumps(
                    {**dict(row), "annotation": json.loads(row["annotation_json"])},
                    ensure_ascii=False,
                )
                + "\n"
                for row in rows
            ),
            encoding="utf-8",
        )
        manifest = {
            "review_id": self.codebook.get("review_id", self.review_contract),
            "coder_id": self.coder_id,
            "generated_at": now(),
            "records": len(rows),
            "reviewed_records": reviewed,
            "queue_sha256": sha256(self.queue_path),
            "codebook_sha256": sha256(self.codebook_path),
            "annotations_sha256": sha256(annotations_path),
        }
        (output_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return output_dir
