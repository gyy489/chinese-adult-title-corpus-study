"""Transactional, resumable two-stage runner for full annotation run 55.

Offline commands: ``prepare``, ``status``, ``plan``, ``finalize``, and
``prepare-deep``. Only ``run`` can contact the provider. ``run`` fails closed
before credential access unless both the run config and central per-operation
authorization explicitly permit the requested wave at the exact config hash.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import signal
import sqlite3
import threading
import time
import uuid
from collections import Counter, defaultdict, deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from src.analysis.build_gendered_argument_visibility_model_benchmark_v1 import (
    atomic_json,
    atomic_jsonl,
    read_jsonl,
    sha256,
    utc_now,
)
from src.analysis.build_gendered_asymmetric_visibility_full_annotation_v1 import (
    CONFIG,
    FRAME,
    ROOT,
    RUN_DIR,
)
from src.analysis.build_gendered_asymmetric_visibility_full_annotation_v1 import (
    MANIFEST as FRAME_MANIFEST,
)
from src.analysis.gendered_argument_visibility_longfield_deep_contract_v1 import (
    validate_contract_files as validate_deep_contract,
)
from src.analysis.gendered_argument_visibility_longfield_deep_contract_v1 import (
    validate_long_item,
)
from src.analysis.gendered_privacy_router_contract_v1 import (
    expand_item as expand_router_item,
)
from src.analysis.gendered_privacy_router_contract_v1 import (
    validate_contract_files as validate_router_contract,
)
from src.analysis.gendered_privacy_router_contract_v1 import (
    validate_item as validate_router_item,
)
from src.analysis.provider_call_authorization_v1 import require_operation
from src.analysis.run_gendered_argument_visibility_model_benchmark_v1 import (
    parse_content,
    pricing_record,
    runner_lock,
    safe_env_key,
    usage_cost_usd,
    worst_case_cost_usd,
)

PRIVATE_DIR = RUN_DIR / "private"
STATE_DIR = RUN_DIR / "state"
STATE_DB = STATE_DIR / "production.sqlite3"
LOCK = STATE_DIR / "runner.lock"
SEALED_DIR = RUN_DIR / "sealed"
NORMALIZED_DIR = RUN_DIR / "normalized_v1"
DEEP_FRAME = PRIVATE_DIR / "deep_candidate_frame.jsonl"
ROUTER_FINAL = PRIVATE_DIR / "router_final_records.jsonl"
DEEP_FINAL = PRIVATE_DIR / "deep_final_records.jsonl"
PROGRESS_MANIFEST = RUN_DIR / "production_progress_manifest.json"
EXPECTED_EXPERIMENT_ID = "55_gendered_asymmetric_visibility_full_annotation_v1"
AUTHORIZATION_OPERATION_PREFIX = "run55"
ROUTER_GATE = (
    ROOT / "results/tables/gendered_privacy_router_model_benchmark_v1/manifest.json"
)
DEEP_BRIDGE_GATE = (
    ROOT
    / "data/interim/54_gendered_argument_visibility_longfield_deep_validation_v1/validation_gate.json"
)
DEEP_NEGATIVE_GATE = (
    ROOT
    / "data/interim/54b_gendered_argument_visibility_longfield_negative_validation_v1/validation_gate.json"
)
_stop = threading.Event()


class ProductionError(RuntimeError):
    """Raised when a production invariant or gate is not satisfied."""


def load_config() -> dict[str, Any]:
    value = json.loads(CONFIG.read_text(encoding="utf-8"))
    if value.get("experiment_id") != EXPECTED_EXPERIMENT_ID:
        raise ProductionError("unexpected production config")
    return value


def connect_state(path: Path | None = None) -> sqlite3.Connection:
    path = STATE_DB if path is None else path
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=DELETE")
    return connection


def initialize_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS items (
            stage TEXT NOT NULL,
            item_index INTEGER NOT NULL,
            wave TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending','in_flight','complete','unresolved')),
            annotation_attempts INTEGER NOT NULL DEFAULT 0,
            provider_calls INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            artifact_path TEXT,
            active_call_id TEXT,
            PRIMARY KEY(stage, item_index)
        );
        CREATE TABLE IF NOT EXISTS totals (
            stage TEXT PRIMARY KEY,
            provider_calls INTEGER NOT NULL DEFAULT 0,
            http_429_calls INTEGER NOT NULL DEFAULT 0,
            http_402_calls INTEGER NOT NULL DEFAULT 0,
            actual_cost_usd REAL NOT NULL DEFAULT 0,
            budget_charged_usd REAL NOT NULL DEFAULT 0,
            reserved_usd REAL NOT NULL DEFAULT 0,
            cache_hit_input_tokens INTEGER NOT NULL DEFAULT 0,
            cache_miss_input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0
        );
        """
    )


def _metadata(connection: sqlite3.Connection) -> dict[str, str]:
    return {
        row["key"]: row["value"]
        for row in connection.execute("SELECT key,value FROM metadata")
    }


def prepare_state(
    path: Path | None = None, frame_path: Path | None = None
) -> dict[str, Any]:
    path = STATE_DB if path is None else path
    frame_path = FRAME if frame_path is None else frame_path
    config = load_config()
    if not frame_path.exists() or not FRAME_MANIFEST.exists():
        raise ProductionError("build the offline production frame first")
    rows = read_jsonl(frame_path)
    if len(rows) != int(config["scope"]["new_router_records"]):
        raise ProductionError("production frame count drifted")
    connection = connect_state(path)
    try:
        initialize_schema(connection)
        metadata = _metadata(connection)
        current_hash = sha256(frame_path)
        if metadata and metadata.get("frame_sha256") != current_hash:
            raise ProductionError("existing state belongs to a different frame")
        if not metadata:
            connection.executemany(
                "INSERT INTO metadata(key,value) VALUES(?,?)",
                [
                    ("experiment_id", config["experiment_id"]),
                    ("config_sha256", sha256(CONFIG)),
                    ("frame_sha256", current_hash),
                    ("created_at", utc_now()),
                ],
            )
            connection.executemany(
                "INSERT INTO items(stage,item_index,wave,status) VALUES('router',?,?, 'pending')",
                [
                    (int(row["production_item_index"]), row["router_wave"])
                    for row in rows
                ],
            )
            connection.execute("INSERT INTO totals(stage) VALUES('router')")
            connection.commit()
        else:
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key,value) VALUES('config_sha256',?)",
                (sha256(CONFIG),),
            )
            connection.commit()
        os.chmod(path, 0o600)
        counts = state_counts(connection)
        return {"prepared": not bool(metadata), "counts": counts, "api_calls": 0}
    finally:
        connection.close()


def state_counts(connection: sqlite3.Connection) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = defaultdict(dict)
    query = "SELECT stage,status,COUNT(*) AS n FROM items GROUP BY stage,status ORDER BY stage,status"
    for row in connection.execute(query):
        result[row["stage"]][row["status"]] = int(row["n"])
    return dict(result)


def _transport_items(value: Any, expected: set[int]) -> list[dict[str, Any]]:
    if not isinstance(value, dict) or set(value) != {"items"}:
        raise ProductionError("response must contain exactly top-level items")
    items = value["items"]
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ProductionError("items must be an array of objects")
    indices = [item.get("i") for item in items]
    if len(indices) != len(set(indices)) or set(indices) != expected:
        raise ProductionError("response index set differs from request")
    return sorted(items, key=lambda item: int(item["i"]))


def normalize_router_raw(
    raw: dict[str, Any], title: str
) -> tuple[dict[str, Any], list[str]]:
    try:
        return validate_router_item(raw), []
    except Exception:
        if (
            set(raw)
            == {"i", "rv", "privacy", "creation", "distribution", "exposure", "t"}
            and raw["t"] == title
        ):
            normalized = {key: value for key, value in raw.items() if key != "t"}
            return validate_router_item(normalized), ["router_echo_title_drop_v1"]
        raise


def build_user_payload(group: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "items": [
            {"i": int(row["production_item_index"]), "t": row["deidentified_title"]}
            for row in group
        ]
    }


def _json_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def provider_call(
    *,
    stage: str,
    wave: str,
    call_id: str,
    group: list[dict[str, Any]],
    api_key: str,
    config: dict[str, Any],
    entry: dict[str, Any],
    system_prompt: str,
    pricing: dict[str, Any],
) -> dict[str, Any]:
    started = time.time()
    expected = {int(row["production_item_index"]) for row in group}
    title_by_index = {
        int(row["production_item_index"]): row["deidentified_title"] for row in group
    }
    payload = build_user_payload(group)
    call_dir = SEALED_DIR / stage / "calls"
    item_dir = SEALED_DIR / stage / "items"
    call_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    item_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    call_record: dict[str, Any] = {
        "call_id": call_id,
        "stage": stage,
        "wave": wave,
        "model": entry["model"],
        "thinking": entry["thinking"],
        "started_at": utc_now(),
        "item_indices": sorted(expected),
        "system_prompt_sha256": hashlib.sha256(
            system_prompt.encode("utf-8")
        ).hexdigest(),
        "user_payload_sha256": _json_hash(payload),
        "pricing": pricing,
        "http_status": None,
        "usage": None,
        "normalized_usage": None,
        "actual_cost_usd": None,
        "provider_message": None,
        "finish_reason": None,
        "error": None,
    }
    response: requests.Response | None = None
    outcomes: list[dict[str, Any]] = []
    try:
        response = requests.post(
            config["runner"]["endpoint"],
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": entry["model"],
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": json.dumps(
                            payload, ensure_ascii=False, separators=(",", ":")
                        ),
                    },
                ],
                "response_format": {"type": "json_object"},
                "temperature": float(config["runner"]["temperature"]),
                "top_p": float(config["runner"]["top_p"]),
                "stream": bool(config["runner"]["stream"]),
                "max_tokens": int(entry["max_output_tokens"]),
                "thinking": {"type": entry["thinking"]},
            },
            timeout=int(config["runner"]["request_timeout_seconds"]),
        )
        call_record["http_status"] = response.status_code
        if response.status_code != 200:
            call_record["error"] = f"HTTP_{response.status_code}"
            return {
                "kind": "http_error",
                "http_status": response.status_code,
                "retry_after": response.headers.get("Retry-After"),
                "outcomes": [],
                "call_id": call_id,
            }
        body = response.json()
        usage = body.get("usage", {})
        cost, normalized_usage = usage_cost_usd(usage, pricing["rates_usd_per_million"])
        call_record["usage"] = usage
        call_record["normalized_usage"] = normalized_usage
        call_record["actual_cost_usd"] = cost
        choice = body["choices"][0]
        message = choice["message"]
        call_record["finish_reason"] = choice.get("finish_reason")
        call_record["provider_message"] = message
        raw_items = _transport_items(parse_content(message["content"]), expected)
        deep_codebook = (
            validate_deep_contract()["codebook"] if stage == "deep" else None
        )
        for raw in raw_items:
            index = int(raw["i"])
            output: dict[str, Any] | None = None
            derived: dict[str, Any] | None = None
            actions: list[str] = []
            try:
                if stage == "router":
                    normalized, actions = normalize_router_raw(
                        raw, title_by_index[index]
                    )
                    output = expand_router_item(normalized)
                else:
                    assert deep_codebook is not None
                    derived = validate_long_item(
                        raw, title_by_index[index], deep_codebook
                    )
                    output = {key: value for key, value in raw.items() if key != "i"}
                valid = True
                error = None
            except Exception as exc:  # noqa: BLE001
                valid = False
                error = f"{type(exc).__name__}:{exc}"
            item_record = {
                "call_id": call_id,
                "stage": stage,
                "wave": wave,
                "completed_at": utc_now(),
                "item_index": index,
                "valid": valid,
                "raw_output": raw,
                "output": output,
                "derived": derived,
                "normalization_actions": actions,
                "error": error,
            }
            item_path = item_dir / f"item_{index:06d}_call_{call_id}.json"
            atomic_json(item_path, item_record, 0o600)
            outcomes.append(
                {
                    "item_index": index,
                    "valid": valid,
                    "error": error,
                    "path": str(item_path.relative_to(ROOT)),
                }
            )
        return {
            "kind": "success",
            "http_status": 200,
            "outcomes": outcomes,
            "call_id": call_id,
            "actual_cost_usd": cost,
            "normalized_usage": normalized_usage,
        }
    except Exception as exc:  # noqa: BLE001
        call_record["error"] = f"{type(exc).__name__}:{exc}"
        return {
            "kind": "batch_error",
            "http_status": response.status_code if response is not None else None,
            "outcomes": [],
            "call_id": call_id,
            "error": call_record["error"],
            "actual_cost_usd": call_record.get("actual_cost_usd"),
            "normalized_usage": call_record.get("normalized_usage"),
        }
    finally:
        call_record["completed_at"] = utc_now()
        call_record["elapsed_s"] = time.time() - started
        atomic_json(call_dir / f"call_{call_id}.json", call_record, 0o600)


def _gate_status(path: Path, *, recommendation: bool = False) -> bool:
    if not path.exists():
        return False
    value = json.loads(path.read_text(encoding="utf-8"))
    if recommendation:
        return (
            value.get("recommendation", {}).get("status") == "gate_passed"
            and value.get("recommendation", {}).get("recommended_config_id")
            == "flash_nonthinking"
        )
    return value.get("all_gates_pass") is True and value.get("status") == "pass"


def assert_stage_gates(stage: str) -> None:
    if stage == "router":
        if not _gate_status(ROUTER_GATE, recommendation=True):
            raise ProductionError("run-53 Flash 200 router gate has not passed")
    elif not (_gate_status(DEEP_BRIDGE_GATE) and _gate_status(DEEP_NEGATIVE_GATE)):
        raise ProductionError(
            "both long-field bridge and privacy-negative gates must pass"
        )


def _stage_waves(config: dict[str, Any], stage: str) -> list[str]:
    key = "router_waves" if stage == "router" else "deep_waves"
    return [str(row["id"]) for row in config["sampling"][key]]


def _recover_interrupted(connection: sqlite3.Connection, stage: str) -> None:
    total = connection.execute(
        "SELECT reserved_usd FROM totals WHERE stage=?", (stage,)
    ).fetchone()
    if total is None:
        raise ProductionError(f"stage is not prepared: {stage}")
    reserved = float(total["reserved_usd"])
    if reserved:
        connection.execute(
            "UPDATE totals SET budget_charged_usd=budget_charged_usd+reserved_usd,reserved_usd=0 WHERE stage=?",
            (stage,),
        )
    connection.execute(
        "UPDATE items SET status='pending',active_call_id=NULL,last_error='interrupted_in_flight_conservatively_budgeted' WHERE stage=? AND status='in_flight'",
        (stage,),
    )
    connection.commit()


def _prior_waves_complete(
    connection: sqlite3.Connection, stage: str, wave: str, config: dict[str, Any]
) -> None:
    waves = _stage_waves(config, stage)
    if wave not in waves:
        raise ProductionError(f"unknown {stage} wave: {wave}")
    prior = waves[: waves.index(wave)]
    if not prior:
        return
    placeholders = ",".join("?" for _ in prior)
    row = connection.execute(
        f"SELECT COUNT(*) AS n FROM items WHERE stage=? AND wave IN ({placeholders}) AND status!='complete'",
        (stage, *prior),
    ).fetchone()
    if int(row["n"]):
        raise ProductionError(
            "a prior wave is incomplete; automatic cross-wave execution is disabled"
        )


def _rows_for_stage(stage: str) -> list[dict[str, Any]]:
    return read_jsonl(FRAME if stage == "router" else DEEP_FRAME)


def _row_index(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {int(row["production_item_index"]): row for row in rows}


def command_prepare(_args: argparse.Namespace) -> None:
    print(json.dumps(prepare_state(), ensure_ascii=False))


def command_status(_args: argparse.Namespace) -> None:
    if not STATE_DB.exists():
        print(json.dumps({"status": "not_prepared", "api_calls": 0}))
        return
    connection = connect_state()
    try:
        totals = [
            dict(row)
            for row in connection.execute("SELECT * FROM totals ORDER BY stage")
        ]
        print(
            json.dumps(
                {
                    "state_counts": state_counts(connection),
                    "totals": totals,
                    "api_calls": 0,
                }
            )
        )
    finally:
        connection.close()


def command_plan(_args: argparse.Namespace) -> None:
    config = load_config()
    router_contract = validate_router_contract()
    longest = sorted(
        read_jsonl(FRAME), key=lambda row: len(row["deidentified_title"]), reverse=True
    )[:20]
    entry = config["model_configs"]["router"]
    plans = []
    for wave in config["sampling"]["router_waves"]:
        cumulative = int(wave["cumulative_records"])
        calls = math.ceil(cumulative / int(entry["batch_size"]))
        estimates = {}
        for profile in config["pricing"]["profiles"]:
            price = pricing_record(
                config, entry["model"], datetime.now(timezone.utc), profile
            )
            per_call = worst_case_cost_usd(
                router_contract["rendered_prompt"],
                build_user_payload(longest),
                entry,
                price["rates_usd_per_million"],
            )
            estimates[profile] = calls * per_call
        plans.append(
            {
                "wave": wave["id"],
                "cumulative_records": cumulative,
                "maximum_calls_no_retry": calls,
                "conservative_worst_case_usd": estimates,
            }
        )
    print(
        json.dumps(
            {
                "status": config["status"],
                "provider_calls_authorized_in_run_config": config["approval"][
                    "provider_calls_authorized"
                ],
                "router": plans,
                "deep": "candidate count and exact call projection become available after router finalize",
                "api_calls": 0,
            },
            ensure_ascii=False,
        )
    )


def command_run(args: argparse.Namespace) -> None:
    if args.limit_records <= 0 or args.limit_calls <= 0 or args.max_usd <= 0:
        raise ProductionError(
            "limit-records, limit-calls, and max-usd must be positive"
        )
    config = load_config()
    if args.workers not in config["runner"]["workers_ladder"]:
        raise ProductionError(
            "workers must be one of the frozen concurrency ladder values"
        )
    stage = args.stage
    wave = args.wave
    if wave not in _stage_waves(config, stage):
        raise ProductionError("wave does not belong to stage")
    assert_stage_gates(stage)
    authorization = require_operation(
        f"{AUTHORIZATION_OPERATION_PREFIX}.{wave}", CONFIG
    )
    if not STATE_DB.exists():
        raise ProductionError("run prepare first")
    if stage == "deep" and not DEEP_FRAME.exists():
        raise ProductionError("run prepare-deep first")
    # Credential access deliberately occurs after every approval and gate check.
    api_key = os.environ.get("DEEPSEEK_API_KEY") or safe_env_key(args.env_file)
    if not api_key:
        raise ProductionError("DEEPSEEK_API_KEY is not set")
    entry = config["model_configs"][stage]
    contract = (
        validate_router_contract() if stage == "router" else validate_deep_contract()
    )
    system_prompt = contract["rendered_prompt"]
    rows = _rows_for_stage(stage)
    by_index = _row_index(rows)
    batch_size = int(entry["batch_size"])
    max_attempts = int(config["runner"]["max_attempts_per_item"])
    _stop.clear()

    def stop_handler(_signum: int, _frame: Any) -> None:
        _stop.set()

    previous_int = signal.signal(signal.SIGINT, stop_handler)
    previous_term = signal.signal(signal.SIGTERM, stop_handler)
    try:
        with runner_lock(LOCK):
            connection = connect_state()
            try:
                _recover_interrupted(connection, stage)
                _prior_waves_complete(connection, stage, wave, config)
                pending = [
                    int(row["item_index"])
                    for row in connection.execute(
                        "SELECT item_index FROM items WHERE stage=? AND wave=? AND status='pending' AND annotation_attempts<? ORDER BY item_index LIMIT ?",
                        (stage, wave, max_attempts, args.limit_records),
                    )
                ]
                queue: deque[list[dict[str, Any]]] = deque(
                    [by_index[index] for index in pending][offset : offset + batch_size]
                    for offset in range(0, len(pending), batch_size)
                )
                futures: dict[Future[dict[str, Any]], dict[str, Any]] = {}
                calls_this_run = 0
                records_dispatched: set[int] = set()
                fused_402 = False
                budget_blocked = False
                with ThreadPoolExecutor(max_workers=args.workers) as executor:
                    while queue or futures:
                        while (
                            queue
                            and len(futures) < args.workers
                            and calls_this_run < args.limit_calls
                            and not _stop.is_set()
                            and not fused_402
                        ):
                            group = queue.popleft()
                            pricing = pricing_record(
                                config,
                                entry["model"],
                                datetime.now(timezone.utc),
                                args.pricing_profile,
                            )
                            worst = worst_case_cost_usd(
                                system_prompt,
                                build_user_payload(group),
                                entry,
                                pricing["rates_usd_per_million"],
                            )
                            totals = connection.execute(
                                "SELECT budget_charged_usd,reserved_usd FROM totals WHERE stage=?",
                                (stage,),
                            ).fetchone()
                            if (
                                float(totals["budget_charged_usd"])
                                + float(totals["reserved_usd"])
                                + worst
                                > args.max_usd + 1e-12
                            ):
                                queue.appendleft(group)
                                budget_blocked = True
                                break
                            call_id = f"{int(time.time())}_{uuid.uuid4().hex}"
                            indices = [
                                int(row["production_item_index"]) for row in group
                            ]
                            placeholders = ",".join("?" for _ in indices)
                            connection.execute(
                                "UPDATE totals SET provider_calls=provider_calls+1,reserved_usd=reserved_usd+? WHERE stage=?",
                                (worst, stage),
                            )
                            connection.execute(
                                f"UPDATE items SET status='in_flight',provider_calls=provider_calls+1,active_call_id=? WHERE stage=? AND item_index IN ({placeholders})",
                                (call_id, stage, *indices),
                            )
                            connection.commit()
                            records_dispatched.update(indices)
                            future = executor.submit(
                                provider_call,
                                stage=stage,
                                wave=wave,
                                call_id=call_id,
                                group=group,
                                api_key=api_key,
                                config=config,
                                entry=entry,
                                system_prompt=system_prompt,
                                pricing=pricing,
                            )
                            futures[future] = {"group": group, "worst": worst}
                            calls_this_run += 1
                        if not futures:
                            break
                        done, _ = wait(set(futures), return_when=FIRST_COMPLETED)
                        for future in done:
                            metadata = futures.pop(future)
                            result = future.result()
                            actual = result.get("actual_cost_usd")
                            charge = (
                                float(actual)
                                if actual is not None
                                else float(metadata["worst"])
                            )
                            usage = result.get("normalized_usage") or {}
                            status = result.get("http_status")
                            connection.execute(
                                "UPDATE totals SET reserved_usd=MAX(0,reserved_usd-?),budget_charged_usd=budget_charged_usd+?,actual_cost_usd=actual_cost_usd+?,http_429_calls=http_429_calls+?,http_402_calls=http_402_calls+?,cache_hit_input_tokens=cache_hit_input_tokens+?,cache_miss_input_tokens=cache_miss_input_tokens+?,output_tokens=output_tokens+? WHERE stage=?",
                                (
                                    metadata["worst"],
                                    charge,
                                    float(actual or 0),
                                    int(status == 429),
                                    int(status == 402),
                                    int(usage.get("cache_hit_input_tokens", 0)),
                                    int(usage.get("cache_miss_input_tokens", 0)),
                                    int(usage.get("output_tokens", 0)),
                                    stage,
                                ),
                            )
                            if status == 402:
                                fused_402 = True
                            outcomes = {
                                int(row["item_index"]): row
                                for row in result.get("outcomes", [])
                            }
                            for row in metadata["group"]:
                                index = int(row["production_item_index"])
                                if result["kind"] == "success":
                                    outcome = outcomes[index]
                                    new_status = (
                                        "complete"
                                        if outcome["valid"]
                                        else (
                                            "unresolved"
                                            if _attempts(connection, stage, index) + 1
                                            >= max_attempts
                                            else "pending"
                                        )
                                    )
                                    connection.execute(
                                        "UPDATE items SET status=?,annotation_attempts=annotation_attempts+1,last_error=?,artifact_path=?,active_call_id=NULL WHERE stage=? AND item_index=?",
                                        (
                                            new_status,
                                            outcome["error"],
                                            outcome["path"]
                                            if outcome["valid"]
                                            else None,
                                            stage,
                                            index,
                                        ),
                                    )
                                elif status in {402, 429} or status is None:
                                    connection.execute(
                                        "UPDATE items SET status='pending',last_error=?,active_call_id=NULL WHERE stage=? AND item_index=?",
                                        (
                                            f"HTTP_{status}"
                                            if status
                                            else result.get("error"),
                                            stage,
                                            index,
                                        ),
                                    )
                                else:
                                    attempts = _attempts(
                                        connection, stage, index
                                    ) + int(status == 200)
                                    new_status = (
                                        "unresolved"
                                        if attempts >= max_attempts
                                        else "pending"
                                    )
                                    connection.execute(
                                        "UPDATE items SET status=?,annotation_attempts=?,last_error=?,active_call_id=NULL WHERE stage=? AND item_index=?",
                                        (
                                            new_status,
                                            attempts,
                                            result.get("error") or f"HTTP_{status}",
                                            stage,
                                            index,
                                        ),
                                    )
                            connection.commit()
                        if budget_blocked and not futures:
                            break
                write_progress_manifest(connection, authorization)
                print(
                    json.dumps(
                        {
                            "stage": stage,
                            "wave": wave,
                            "calls_this_run": calls_this_run,
                            "unique_records_dispatched": len(records_dispatched),
                            "state_counts": state_counts(connection),
                            "stopped_402": fused_402,
                            "budget_blocked": budget_blocked,
                        }
                    )
                )
            finally:
                connection.close()
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)


def _attempts(connection: sqlite3.Connection, stage: str, index: int) -> int:
    row = connection.execute(
        "SELECT annotation_attempts FROM items WHERE stage=? AND item_index=?",
        (stage, index),
    ).fetchone()
    return int(row["annotation_attempts"])


def write_progress_manifest(
    connection: sqlite3.Connection, authorization: dict[str, Any] | None = None
) -> None:
    artifacts: dict[str, str] = {}
    for path in (FRAME, DEEP_FRAME, ROUTER_FINAL, DEEP_FINAL):
        if path.exists():
            artifacts[str(path.relative_to(ROOT))] = sha256(path)
    value = {
        "updated_at": utc_now(),
        "experiment_id": load_config()["experiment_id"],
        "state_counts": state_counts(connection),
        "totals": [
            dict(row)
            for row in connection.execute("SELECT * FROM totals ORDER BY stage")
        ],
        "config_sha256": sha256(CONFIG),
        "state_db_sha256": sha256(STATE_DB),
        "artifacts": artifacts,
        "last_authorization": authorization,
        "privacy": "No titles, record IDs, evidence, or provider messages are included in this public manifest.",
    }
    atomic_json(PROGRESS_MANIFEST, value)


def _all_complete(connection: sqlite3.Connection, stage: str) -> bool:
    row = connection.execute(
        "SELECT COUNT(*) AS n FROM items WHERE stage=? AND status!='complete'", (stage,)
    ).fetchone()
    return int(row["n"]) == 0


def command_finalize(args: argparse.Namespace) -> None:
    if not STATE_DB.exists():
        raise ProductionError("run prepare first")
    stage = args.stage
    connection = connect_state()
    try:
        source_rows = _row_index(_rows_for_stage(stage))
        output_rows: list[dict[str, Any]] = []
        for state in connection.execute(
            "SELECT * FROM items WHERE stage=? AND status='complete' ORDER BY item_index",
            (stage,),
        ):
            artifact = json.loads(
                (ROOT / state["artifact_path"]).read_text(encoding="utf-8")
            )
            source = source_rows[int(state["item_index"])]
            output_rows.append(
                {
                    "production_item_index": int(state["item_index"]),
                    "full_item_index": int(source["full_item_index"]),
                    "record_id": source["record_id"],
                    "title_sha256": source["title_sha256"],
                    "stage": stage,
                    "wave": state["wave"],
                    "annotation_version": "router_v1"
                    if stage == "router"
                    else "longfield_deep_v1",
                    "output": artifact["output"],
                    "derived": artifact.get("derived"),
                    "normalization_actions": artifact.get("normalization_actions", []),
                    "artifact_path": state["artifact_path"],
                }
            )
        destination = ROUTER_FINAL if stage == "router" else DEEP_FINAL
        atomic_jsonl(destination, output_rows, 0o600)
        write_progress_manifest(connection)
        print(
            json.dumps(
                {
                    "stage": stage,
                    "finalized_records": len(output_rows),
                    "complete": _all_complete(connection, stage),
                    "output_sha256": sha256(destination),
                    "api_calls": 0,
                }
            )
        )
    finally:
        connection.close()


def _assign_deep_waves(
    candidates: list[dict[str, Any]], config: dict[str, Any]
) -> list[dict[str, Any]]:
    candidates.sort(
        key=lambda row: (row["deep_order_hash"], int(row["production_item_index"]))
    )
    cutoffs = [500, 5000]
    for position, row in enumerate(candidates, start=1):
        if position <= min(cutoffs[0], len(candidates)):
            wave = "deep_gate_00500"
        elif position <= min(cutoffs[1], len(candidates)):
            wave = "deep_ramp_05000"
        else:
            wave = "deep_full"
        row["deep_wave"] = wave
    return candidates


def command_prepare_deep(_args: argparse.Namespace) -> None:
    assert_stage_gates("deep")
    if not ROUTER_FINAL.exists() or not STATE_DB.exists():
        raise ProductionError("complete and finalize the router first")
    connection = connect_state()
    try:
        if not _all_complete(connection, "router"):
            expected = int(load_config()["scope"]["new_router_records"])
            raise ProductionError(f"all {expected:,} router items must be complete")
        frame = _row_index(read_jsonl(FRAME))
        router = read_jsonl(ROUTER_FINAL)
        candidates = []
        for record in router:
            output = record["output"]
            if output["record_validity"] == "valid" and output[
                "privacy_chain_relevance"
            ] in {"relevant", "unclear"}:
                candidates.append(dict(frame[int(record["production_item_index"])]))
        _assign_deep_waves(candidates, load_config())
        atomic_jsonl(DEEP_FRAME, candidates, 0o600)
        initialize_schema(connection)
        existing = connection.execute(
            "SELECT COUNT(*) AS n FROM items WHERE stage='deep'"
        ).fetchone()
        if int(existing["n"]):
            metadata = _metadata(connection)
            if metadata.get("deep_frame_sha256") != sha256(DEEP_FRAME):
                raise ProductionError(
                    "existing deep state belongs to a different candidate frame"
                )
            prepared = False
        else:
            connection.executemany(
                "INSERT INTO items(stage,item_index,wave,status) VALUES('deep',?,?, 'pending')",
                [
                    (int(row["production_item_index"]), row["deep_wave"])
                    for row in candidates
                ],
            )
            connection.execute("INSERT INTO totals(stage) VALUES('deep')")
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key,value) VALUES('deep_frame_sha256',?)",
                (sha256(DEEP_FRAME),),
            )
            connection.commit()
            prepared = True
        counts = Counter(row["deep_wave"] for row in candidates)
        write_progress_manifest(connection)
        print(
            json.dumps(
                {
                    "prepared": prepared,
                    "eligible_records": len(candidates),
                    "wave_counts": dict(sorted(counts.items())),
                    "api_calls": 0,
                }
            )
        )
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("prepare", "status", "plan", "prepare-deep"):
        subparsers.add_parser(command)
    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("--stage", choices=("router", "deep"), required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--stage", choices=("router", "deep"), required=True)
    run.add_argument("--wave", required=True)
    run.add_argument("--workers", type=int, required=True)
    run.add_argument("--limit-records", type=int, required=True)
    run.add_argument("--limit-calls", type=int, required=True)
    run.add_argument("--max-usd", type=float, required=True)
    run.add_argument(
        "--pricing-profile", choices=("current", "new_offpeak", "new_peak")
    )
    run.add_argument("--env-file", type=Path, default=ROOT / ".env.ai-coding")
    args = parser.parse_args()
    {
        "prepare": command_prepare,
        "status": command_status,
        "plan": command_plan,
        "run": command_run,
        "finalize": command_finalize,
        "prepare-deep": command_prepare_deep,
    }[args.command](args)


if __name__ == "__main__":
    main()
