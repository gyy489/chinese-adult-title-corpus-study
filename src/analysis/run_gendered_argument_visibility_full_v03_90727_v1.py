"""Transactional exact-v0.3 production runner for the 90,727-record corpus.

The verified run-55 SQLite transaction engine is reused for dispatch and
recovery, while this module replaces the failed router contract with the exact
frozen v0.3 contract.  Only ``run`` can contact DeepSeek, and every wave remains
fail-closed behind the central authorization file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import requests

from src.analysis import run_gendered_asymmetric_visibility_full_annotation_v1 as core
from src.analysis.build_gendered_argument_visibility_full_v03_90727_v1 import (
    CONFIG,
    FRAME,
    ROOT,
    RUN_DIR,
)
from src.analysis.build_gendered_argument_visibility_full_v03_90727_v1 import (
    MANIFEST as FRAME_MANIFEST,
)
from src.analysis.build_gendered_argument_visibility_model_benchmark_v1 import (
    atomic_json,
    atomic_jsonl,
    read_jsonl,
    sha256,
    utc_now,
)
from src.analysis.gendered_argument_visibility_contract_v0_3 import (
    validate_contract_files,
    validate_output,
)
from src.analysis.provider_call_authorization_v1 import require_operation
from src.analysis.run_gendered_argument_visibility_model_benchmark_v1 import (
    parse_content,
    usage_cost_usd,
)
from src.analysis.run_gendered_argument_visibility_pilot_v1 import validate_transport
from src.analysis.run_gendered_argument_visibility_scaleup_5000_v1 import (
    normalize_output,
)

PRIVATE_DIR = RUN_DIR / "private"
STATE_DIR = RUN_DIR / "state"
STATE_DB = STATE_DIR / "production.sqlite3"
LOCK = STATE_DIR / "runner.lock"
SEALED_DIR = RUN_DIR / "sealed"
NORMALIZED_DIR = RUN_DIR / "normalized_v1"
UNUSED_DEEP_FRAME = PRIVATE_DIR / "unused_deep_frame.jsonl"
UNUSED_ROUTER_FINAL = PRIVATE_DIR / "unused_router_final.jsonl"
UNUSED_DEEP_FINAL = PRIVATE_DIR / "unused_deep_final.jsonl"
PRIVATE_FINAL = PRIVATE_DIR / "new_v03_final_records.jsonl"
PROGRESS_MANIFEST = RUN_DIR / "production_progress_manifest.json"
FINAL_MANIFEST = RUN_DIR / "final_manifest.json"
EXPECTED_EXPERIMENT_ID = "59_gendered_argument_visibility_full_v03_90727_v1"
AUTHORIZATION_OPERATION_PREFIX = "run59"
_ACTIVE_BATCH_SIZE = 10


class ProductionError(RuntimeError):
    """Raised before credential access when a run-59 invariant fails."""


def load_config() -> dict[str, Any]:
    value = json.loads(CONFIG.read_text(encoding="utf-8"))
    if value.get("experiment_id") != EXPECTED_EXPERIMENT_ID:
        raise ProductionError("unexpected run-59 config")
    # The verified run-55 engine calls its single dispatch stage ``router``.
    # These in-memory aliases do not introduce a routing model or a second stage.
    value["scope"]["new_router_records"] = value["scope"]["new_annotation_records"]
    value["sampling"]["router_waves"] = value["sampling"]["annotation_waves"]
    value["sampling"]["deep_waves"] = []
    value["model_configs"]["router"] = dict(value["model_configs"]["annotation"])
    value["model_configs"]["router"]["batch_size"] = _ACTIVE_BATCH_SIZE
    return value


def build_user_payload(group: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "items": [
            {
                "item_index": int(row["production_item_index"]),
                "deidentified_title": row["deidentified_title"],
            }
            for row in group
        ]
    }


def _json_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
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
    del stage
    started = time.time()
    expected = [int(row["production_item_index"]) for row in group]
    title_by_index = {
        int(row["production_item_index"]): row["deidentified_title"] for row in group
    }
    payload = build_user_payload(group)
    call_dir = SEALED_DIR / "v03/calls"
    item_dir = SEALED_DIR / "v03/items"
    call_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    item_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    call_record: dict[str, Any] = {
        "call_id": call_id,
        "stage": "v03",
        "wave": wave,
        "model": entry["model"],
        "thinking": entry["thinking"],
        "started_at": utc_now(),
        "item_indices": expected,
        "system_prompt_sha256": hashlib.sha256(system_prompt.encode("utf-8")).hexdigest(),
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
    try:
        response = requests.post(
            config["runner"]["endpoint"],
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": entry["model"],
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
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
        call_record["finish_reason"] = choice.get("finish_reason")
        call_record["provider_message"] = choice["message"]
        parsed = parse_content(choice["message"]["content"])
        transported = validate_transport(parsed, expected)
        codebook = validate_contract_files()["codebook"]
        outcomes: list[dict[str, Any]] = []
        for index, raw in transported:
            try:
                output, actions = normalize_output(raw)
                derived = validate_output(output, title_by_index[index], codebook)
                valid = True
                error = None
            except Exception as exc:  # noqa: BLE001
                output = None
                actions = []
                derived = None
                valid = False
                error = f"{type(exc).__name__}:{exc}"
            item_record = {
                "item_index": index,
                "call_id": call_id,
                "wave": wave,
                "completed_at": utc_now(),
                "valid": valid,
                "raw_output": raw,
                "output": output,
                "derived": derived,
                "normalization_actions": actions,
                "error": error,
            }
            path = item_dir / f"item_{index:06d}_call_{call_id}.json"
            atomic_json(path, item_record, 0o600)
            outcomes.append(
                {
                    "item_index": index,
                    "valid": valid,
                    "error": error,
                    "path": str(path.relative_to(ROOT)),
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


def assert_stage_gates(stage: str) -> None:
    if stage != "router":
        raise ProductionError("run 59 has one exact-v0.3 stage only")
    baseline = ROOT / "data/interim/51_gendered_argument_visibility_scaleup_5000_v1/validation_5000_final_manifest.json"
    if not baseline.exists():
        raise ProductionError("frozen 5,000-record v0.3 baseline is missing")
    value = json.loads(baseline.read_text(encoding="utf-8"))
    if value.get("records") != 5000 or value.get("structurally_valid") != 4998:
        raise ProductionError("frozen v0.3 baseline gate drifted")
    failed_optimization = ROOT / "data/interim/58_gendered_argument_visibility_v03_nonthinking_equivalence_v1/smoke_gate.json"
    if not failed_optimization.exists() or json.loads(failed_optimization.read_text(encoding="utf-8")).get("status") != "fail":
        raise ProductionError("thinking-disabled candidate failure is not frozen")


_BINDINGS = {
    "CONFIG": CONFIG,
    "FRAME": FRAME,
    "ROOT": ROOT,
    "RUN_DIR": RUN_DIR,
    "FRAME_MANIFEST": FRAME_MANIFEST,
    "PRIVATE_DIR": PRIVATE_DIR,
    "STATE_DIR": STATE_DIR,
    "STATE_DB": STATE_DB,
    "LOCK": LOCK,
    "SEALED_DIR": SEALED_DIR,
    "NORMALIZED_DIR": NORMALIZED_DIR,
    "DEEP_FRAME": UNUSED_DEEP_FRAME,
    "ROUTER_FINAL": UNUSED_ROUTER_FINAL,
    "DEEP_FINAL": UNUSED_DEEP_FINAL,
    "PROGRESS_MANIFEST": PROGRESS_MANIFEST,
    "EXPECTED_EXPERIMENT_ID": EXPECTED_EXPERIMENT_ID,
    "AUTHORIZATION_OPERATION_PREFIX": AUTHORIZATION_OPERATION_PREFIX,
    "load_config": load_config,
    "validate_router_contract": validate_contract_files,
    "build_user_payload": build_user_payload,
    "provider_call": provider_call,
    "assert_stage_gates": assert_stage_gates,
}


@contextmanager
def bound_core() -> Iterator[None]:
    previous = {name: getattr(core, name) for name in _BINDINGS}
    try:
        for name, value in _BINDINGS.items():
            setattr(core, name, value)
        yield
    finally:
        for name, value in previous.items():
            setattr(core, name, value)


def prepare_state() -> dict[str, Any]:
    with bound_core():
        return core.prepare_state(STATE_DB, FRAME)


def status() -> None:
    with bound_core():
        core.command_status(argparse.Namespace())


def _existing_wave_calls(wave: str) -> int:
    count = 0
    for path in (SEALED_DIR / "v03/calls").glob("call_*.json"):
        value = json.loads(path.read_text(encoding="utf-8"))
        count += int(value.get("wave") == wave)
    return count


def _assert_run_authorization(args: argparse.Namespace) -> None:
    authorization = require_operation(f"{AUTHORIZATION_OPERATION_PREFIX}.{args.wave}", CONFIG)
    approved = authorization.get("approved_operation")
    if not isinstance(approved, dict):
        raise TypeError("authorization does not expose operation limits")
    if args.limit_records > int(approved["record_limit"]):
        raise ProductionError("record limit exceeds authorization")
    if _existing_wave_calls(args.wave) + args.limit_calls > int(approved["call_limit"]):
        raise ProductionError("cumulative wave call limit exceeds authorization")
    if args.max_usd > float(approved["cumulative_usd_limit"]) + 1e-12:
        raise ProductionError("USD limit exceeds authorization")
    if args.pricing_profile != approved.get("pricing_profile"):
        raise ProductionError("pricing profile differs from authorization")
    if int(approved.get("workers", args.workers)) != args.workers:
        raise ProductionError("worker count differs from authorization")
    if int(approved.get("batch_size", args.batch_size)) != args.batch_size:
        raise ProductionError("batch size differs from authorization")


def run(args: argparse.Namespace) -> None:
    global _ACTIVE_BATCH_SIZE
    config = load_config()
    if args.wave not in config["wave_policy"]:
        raise ProductionError("unknown run-59 wave")
    allowed_batches = {10, *config["wave_policy"]["failure_only_retry_batch_sizes"]}
    if args.batch_size not in allowed_batches:
        raise ProductionError("batch size is outside the frozen ladder")
    expected_workers = int(config["wave_policy"][args.wave]["workers"])
    if args.batch_size == 10 and args.workers != expected_workers:
        raise ProductionError("initial wave must use its frozen worker count")
    if args.batch_size < 10 and args.workers > expected_workers:
        raise ProductionError("retry workers cannot exceed the wave worker count")
    _assert_run_authorization(args)
    _ACTIVE_BATCH_SIZE = args.batch_size
    core_args = argparse.Namespace(**vars(args), stage="router")
    with bound_core():
        core.command_run(core_args)


def finalize() -> dict[str, Any]:
    if not STATE_DB.exists():
        raise ProductionError("run prepare first")
    frame = {int(row["production_item_index"]): row for row in read_jsonl(FRAME)}
    connection = core.connect_state(STATE_DB)
    try:
        core.initialize_schema(connection)
        states = list(connection.execute("SELECT * FROM items WHERE stage='router' ORDER BY item_index"))
        output: list[dict[str, Any]] = []
        counts: Counter[str] = Counter()
        for state_row in states:
            index = int(state_row["item_index"])
            source = frame[index]
            artifact_path = state_row["artifact_path"]
            artifact = json.loads((ROOT / artifact_path).read_text(encoding="utf-8")) if artifact_path else None
            valid = state_row["status"] == "complete" and artifact is not None and artifact.get("valid") is True
            counts[state_row["status"]] += 1
            output.append(
                {
                    "production_item_index": index,
                    "full_item_index": int(source["full_item_index"]),
                    "record_id": source["record_id"],
                    "title_sha256": source["title_sha256"],
                    "wave": state_row["wave"],
                    "status": state_row["status"],
                    "valid": valid,
                    "annotation_version": "gendered_argument_visibility_v0.3.0_exact_thinking",
                    "output": artifact.get("output") if valid else None,
                    "derived": artifact.get("derived") if valid else None,
                    "normalization_actions": artifact.get("normalization_actions", []) if artifact else [],
                    "error": None if valid else state_row["last_error"],
                    "artifact_path": artifact_path,
                }
            )
        atomic_jsonl(PRIVATE_FINAL, output, 0o600)
        totals = dict(connection.execute("SELECT * FROM totals WHERE stage='router'").fetchone())
        if counts.get("pending") or counts.get("in_flight"):
            final_status = "incomplete"
        elif counts.get("unresolved"):
            final_status = "complete_with_unresolved"
        else:
            final_status = "complete"
        manifest = {
            "created_at": utc_now(),
            "status": final_status,
            "experiment_id": EXPECTED_EXPERIMENT_ID,
            "records": len(output),
            "state_counts": dict(sorted(counts.items())),
            "totals": totals,
            "provider_calls": int(totals["provider_calls"]),
            "actual_cost_usd": float(totals["actual_cost_usd"]),
            "output": {str(PRIVATE_FINAL.relative_to(ROOT)): sha256(PRIVATE_FINAL)},
            "inputs": {str(CONFIG.relative_to(ROOT)): sha256(CONFIG), str(FRAME.relative_to(ROOT)): sha256(FRAME)},
            "api_calls_during_finalize": 0,
        }
        atomic_json(FINAL_MANIFEST, manifest)
        return manifest
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare")
    sub.add_parser("status")
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--wave", required=True)
    run_parser.add_argument("--workers", type=int, required=True)
    run_parser.add_argument("--batch-size", type=int, required=True)
    run_parser.add_argument("--limit-records", type=int, required=True)
    run_parser.add_argument("--limit-calls", type=int, required=True)
    run_parser.add_argument("--max-usd", type=float, required=True)
    run_parser.add_argument("--pricing-profile", choices=("current", "new_offpeak", "new_peak"), required=True)
    run_parser.add_argument("--env-file", type=Path, default=ROOT / ".env.ai-coding")
    sub.add_parser("finalize")
    args = parser.parse_args()
    if args.command == "prepare":
        print(json.dumps(prepare_state()))
    elif args.command == "status":
        status()
    elif args.command == "run":
        run(args)
    else:
        print(json.dumps(finalize()))


if __name__ == "__main__":
    main()
