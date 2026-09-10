"""Fail-closed DeepSeek low-effort paired gate for the exact v0.3 contract.

Only ``run`` contacts DeepSeek. The runner enforces the real pricing window,
seals every response, accounts directly in CNY, and cannot cross from the
20-record catastrophic gate to 100 records without an offline passing gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from datetime import time as datetime_time
from pathlib import Path
from typing import Any

import requests

from src.analysis.build_gendered_argument_visibility_model_benchmark_v1 import (
    ROOT,
    atomic_json,
    atomic_jsonl,
    read_jsonl,
    sha256,
    utc_now,
)
from src.analysis.gendered_argument_visibility_contract_v0_3 import (
    DEFAULT_CODEBOOK,
    DEFAULT_PROMPT,
    DEFAULT_SCHEMA,
    load_json,
    validate_contract_files,
    validate_output,
)
from src.analysis.provider_call_authorization_v1 import require_operation
from src.analysis.run_gendered_argument_visibility_model_benchmark_v1 import (
    normalized_usage,
    parse_content,
    runner_lock,
)
from src.analysis.run_gendered_argument_visibility_pilot_v1 import validate_transport
from src.analysis.run_gendered_argument_visibility_scaleup_5000_v1 import (
    NORMALIZATION_POLICY,
    normalize_output,
)
from src.analysis.run_gendered_argument_visibility_v03_nonthinking_equivalence_v1 import (
    safe_env_named_key,
)

CONFIG = ROOT / "config/gendered_argument_visibility_v03_low_effort_gate_v1.json"
SOURCE_ITEMS = (
    ROOT
    / "data/interim/58_gendered_argument_visibility_v03_nonthinking_equivalence_v1/private/benchmark_items.jsonl"
)
RUN_DIR = ROOT / "data/interim/69_gendered_argument_visibility_v03_low_effort_gate_v1"
STATE_DIR = RUN_DIR / "state"
STATE = STATE_DIR / "items.json"
LOCK = STATE_DIR / "runner.lock"
SEALED_CALLS = RUN_DIR / "sealed/calls"
SEALED_ITEMS = RUN_DIR / "sealed/items"
PRIVATE_FINAL = RUN_DIR / "private/final_records.jsonl"
PROVIDER_MANIFEST = RUN_DIR / "provider_run_manifest.json"
STAGE_A_GATE = RUN_DIR / "stage_a_gate.json"
AUTHORIZATION_OPERATION = "run69.v03_low_effort_gate_100"
EXPECTED_EXPERIMENT_ID = "69_gendered_argument_visibility_v03_low_effort_gate_v1"
RUNNER = Path(__file__).resolve()


class LowEffortGateError(RuntimeError):
    """Raised before credential access when a frozen gate invariant fails."""


def config_value() -> dict[str, Any]:
    value = load_json(CONFIG)
    model = value["model"]
    if (
        value.get("experiment_id") != EXPECTED_EXPERIMENT_ID
        or value.get("records") != 100
        or value.get("stage_a_records") != 20
        or model.get("model") != "deepseek-v4-flash"
        or model.get("thinking") != "enabled"
        or model.get("reasoning_effort") != "low"
        or model.get("batch_size") != 5
        or model.get("workers") != 8
        or model.get("max_output_tokens") != 64_000
        or value.get("pricing", {}).get("unit") != "CNY_per_million_tokens"
    ):
        raise LowEffortGateError("run-69 fixed design drifted")
    return value


def benchmark_rows() -> list[dict[str, Any]]:
    rows = read_jsonl(SOURCE_ITEMS)
    if len(rows) != 100:
        raise LowEffortGateError("run 69 requires exactly 100 frozen records")
    if [int(row["benchmark_item_index"]) for row in rows] != list(range(1, 101)):
        raise LowEffortGateError("benchmark indices must be contiguous 1..100")
    if any(row.get("frozen_split") == "final_blind_test" for row in rows):
        raise LowEffortGateError("protected final-blind data entered run 69")
    return rows


def input_hashes() -> dict[str, str]:
    paths = (
        CONFIG,
        SOURCE_ITEMS,
        DEFAULT_CODEBOOK,
        DEFAULT_SCHEMA,
        DEFAULT_PROMPT,
        NORMALIZATION_POLICY,
        ROOT / "src/analysis/provider_call_authorization_v1.py",
        RUNNER,
    )
    return {str(path.relative_to(ROOT)): sha256(path) for path in paths}


def _json_sha256(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def state_counts(state: dict[str, Any]) -> dict[str, int]:
    return dict(sorted(Counter(row["status"] for row in state["items"].values()).items()))


def fresh_state() -> dict[str, Any]:
    return {
        "version": "1.0.0",
        "experiment_id": EXPECTED_EXPERIMENT_ID,
        "created_at": utc_now(),
        "input_hashes": input_hashes(),
        "items": {
            str(index): {
                "item_index": index,
                "status": "pending",
                "provider_calls": 0,
                "last_error": None,
                "sealed_item_path": None,
            }
            for index in range(1, 101)
        },
        "totals": {
            "provider_calls": 0,
            "http_402_calls": 0,
            "http_429_calls": 0,
            "actual_cost_cny": 0.0,
            "budget_charged_cny": 0.0,
            "usage": {
                "cache_hit_input_tokens": 0,
                "cache_miss_input_tokens": 0,
                "output_tokens": 0,
                "reasoning_tokens": 0,
            },
            "pricing_profiles_used": {},
        },
        "last_run": None,
    }


def build_payload(group: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "items": [
            {
                "item_index": int(row["benchmark_item_index"]),
                "deidentified_title": row["deidentified_title"],
            }
            for row in group
        ]
    }


def actual_pricing_profile(config: dict[str, Any], now: datetime) -> str:
    transition = datetime.fromisoformat(config["pricing"]["transition_utc"].replace("Z", "+00:00"))
    current = now.astimezone(timezone.utc)
    if current < transition:
        return "current"
    clock = current.time().replace(tzinfo=None)
    for start, end in config["pricing"]["new_peak_windows_utc"]:
        if datetime_time.fromisoformat(start) <= clock < datetime_time.fromisoformat(end):
            return "new_peak"
    return "new_offpeak"


def pricing_record(config: dict[str, Any], requested: str, now: datetime) -> dict[str, Any]:
    actual = actual_pricing_profile(config, now)
    if requested != actual:
        raise LowEffortGateError(
            f"pricing clock mismatch: requested {requested}, actual {actual}"
        )
    entry = config["pricing"]["profiles"][actual]
    return {
        "pricing_version": config["pricing"]["pricing_version"],
        "profile": actual,
        "effective": entry["effective"],
        "rates_cny_per_million": entry[config["model"]["model"]],
    }


def usage_cost_cny(usage: Any, rates: dict[str, float]) -> tuple[float | None, dict[str, int] | None]:
    normalized = normalized_usage(usage)
    if normalized is None:
        return None, None
    details = usage.get("completion_tokens_details") if isinstance(usage, dict) else None
    reasoning = int((details or {}).get("reasoning_tokens", 0) or 0)
    normalized["reasoning_tokens"] = reasoning
    cost = (
        normalized["cache_hit_input_tokens"] * float(rates["cache_hit_input"])
        + normalized["cache_miss_input_tokens"] * float(rates["cache_miss_input"])
        + normalized["output_tokens"] * float(rates["output"])
    ) / 1_000_000
    return cost, normalized


def worst_case_cost_cny(
    system_prompt: str,
    payload: dict[str, Any],
    model: dict[str, Any],
    rates: dict[str, float],
) -> float:
    request_bytes = len(system_prompt.encode("utf-8")) + len(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    input_upper = request_bytes + 512
    return (
        input_upper * float(rates["cache_miss_input"])
        + int(model["max_output_tokens"]) * float(rates["output"])
    ) / 1_000_000


def provider_call(
    *,
    group: list[dict[str, Any]],
    api_key: str,
    config: dict[str, Any],
    system_prompt: str,
    pricing: dict[str, Any],
) -> dict[str, Any]:
    call_id = f"{int(time.time())}_{uuid.uuid4().hex}"
    expected = [int(row["benchmark_item_index"]) for row in group]
    payload = build_payload(group)
    started = time.time()
    record: dict[str, Any] = {
        "call_id": call_id,
        "experiment_id": EXPECTED_EXPERIMENT_ID,
        "model": config["model"]["model"],
        "thinking": "enabled",
        "reasoning_effort": "low",
        "started_at": utc_now(),
        "item_indices": expected,
        "system_prompt_sha256": hashlib.sha256(system_prompt.encode("utf-8")).hexdigest(),
        "user_payload_sha256": _json_sha256(payload),
        "pricing": pricing,
        "http_status": None,
        "usage": None,
        "normalized_usage": None,
        "actual_cost_cny": None,
        "finish_reason": None,
        "provider_message": None,
        "error": None,
    }
    response: requests.Response | None = None
    try:
        response = requests.post(
            config["runner"]["endpoint"],
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": config["model"]["model"],
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                "response_format": {"type": "json_object"},
                "stream": False,
                "max_tokens": int(config["model"]["max_output_tokens"]),
                "thinking": {"type": "enabled"},
                "reasoning_effort": "low",
            },
            timeout=int(config["runner"]["request_timeout_seconds"]),
        )
        record["http_status"] = response.status_code
        if response.status_code != 200:
            record["error"] = f"HTTP_{response.status_code}"
            return {"kind": "http_error", "status": response.status_code, "call_id": call_id}
        body = response.json()
        usage = body.get("usage", {})
        cost, normalized = usage_cost_cny(usage, pricing["rates_cny_per_million"])
        record["usage"] = usage
        record["normalized_usage"] = normalized
        record["actual_cost_cny"] = cost
        choice = body["choices"][0]
        record["finish_reason"] = choice.get("finish_reason")
        record["provider_message"] = choice["message"]
        if record["finish_reason"] not in set(config["scoring"]["gates"]["allowed_finish_reasons"]):
            raise LowEffortGateError(f"disallowed finish reason: {record['finish_reason']}")
        transported = validate_transport(parse_content(choice["message"]["content"]), expected)
        codebook = validate_contract_files()["codebook"]
        titles = {int(row["benchmark_item_index"]): row["deidentified_title"] for row in group}
        outcomes: list[dict[str, Any]] = []
        for index, raw in transported:
            try:
                output, actions = normalize_output(raw)
                derived = validate_output(output, titles[index], codebook)
                valid, error = True, None
            except Exception as exc:  # noqa: BLE001
                output, actions, derived = None, [], None
                valid, error = False, f"{type(exc).__name__}:{exc}"
            item = {
                "item_index": index,
                "call_id": call_id,
                "completed_at": utc_now(),
                "valid": valid,
                "raw_output": raw,
                "output": output,
                "derived": derived,
                "normalization_actions": actions,
                "error": error,
            }
            path = SEALED_ITEMS / f"item_{index:03d}_call_{call_id}.json"
            atomic_json(path, item, 0o600)
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
            "status": 200,
            "call_id": call_id,
            "outcomes": outcomes,
            "actual_cost_cny": float(cost or 0),
            "normalized_usage": normalized,
        }
    except Exception as exc:  # noqa: BLE001
        record["error"] = f"{type(exc).__name__}:{exc}"
        return {
            "kind": "batch_error",
            "status": response.status_code if response is not None else None,
            "call_id": call_id,
            "error": record["error"],
            "actual_cost_cny": float(record.get("actual_cost_cny") or 0),
            "normalized_usage": record.get("normalized_usage"),
        }
    finally:
        record["completed_at"] = utc_now()
        record["elapsed_s"] = time.time() - started
        atomic_json(SEALED_CALLS / f"call_{call_id}.json", record, 0o600)


def write_manifest(state: dict[str, Any]) -> None:
    calls = sorted(SEALED_CALLS.glob("call_*.json"))
    items = sorted(SEALED_ITEMS.glob("item_*.json"))
    manifest: dict[str, Any] = {
        "updated_at": utc_now(),
        "status": "complete" if state_counts(state).get("complete", 0) == 100 else "incomplete",
        "experiment_id": EXPECTED_EXPERIMENT_ID,
        "model": "deepseek-v4-flash",
        "thinking": "enabled",
        "reasoning_effort": "low",
        "state_counts": state_counts(state),
        **state["totals"],
        "last_run": state.get("last_run"),
        "inputs": input_hashes(),
        "sealed_artifacts": {
            "calls": len(calls),
            "items": len(items),
            "call_sha256": {str(path.relative_to(ROOT)): sha256(path) for path in calls},
            "item_sha256": {str(path.relative_to(ROOT)): sha256(path) for path in items},
        },
        "privacy": "No titles, record IDs, evidence spans, Codex labels, or provider messages are public.",
    }
    if PRIVATE_FINAL.exists():
        manifest["private_final_sha256"] = sha256(PRIVATE_FINAL)
    atomic_json(PROVIDER_MANIFEST, manifest)


def command_prepare(_args: argparse.Namespace) -> None:
    config_value()
    validate_contract_files()
    benchmark_rows()
    for path in (STATE_DIR, SEALED_CALLS, SEALED_ITEMS, PRIVATE_FINAL.parent):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.chmod(0o700)
    if STATE.exists():
        state = load_json(STATE)
        if state.get("input_hashes") != input_hashes():
            if state["totals"]["provider_calls"] or state_counts(state) != {"pending": 100}:
                raise LowEffortGateError("non-empty run-69 state belongs to different inputs")
            state = fresh_state()
            atomic_json(STATE, state, 0o600)
    else:
        state = fresh_state()
        atomic_json(STATE, state, 0o600)
    write_manifest(state)
    print(json.dumps({"prepared": 100, "provider_calls": 0}))


def command_status(_args: argparse.Namespace) -> None:
    if not STATE.exists():
        print(json.dumps({"status": "not_prepared"}))
        return
    state = load_json(STATE)
    print(
        json.dumps(
            {
                "state_counts": state_counts(state),
                "provider_calls": state["totals"]["provider_calls"],
                "actual_cost_cny": state["totals"]["actual_cost_cny"],
                "usage": state["totals"]["usage"],
                "last_run": state.get("last_run"),
            }
        )
    )


def _assert_authorized(args: argparse.Namespace, authorization: dict[str, Any]) -> None:
    approved = authorization.get("approved_operation")
    if not isinstance(approved, dict):
        raise LowEffortGateError("authorization limits are missing")
    if args.target_records > int(approved["record_limit"]):
        raise LowEffortGateError("record limit exceeds authorization")
    if args.limit_calls > int(approved["call_limit"]):
        raise LowEffortGateError("call limit exceeds authorization")
    if args.max_cny > float(approved["cumulative_cny_limit"]) + 1e-12:
        raise LowEffortGateError("CNY limit exceeds authorization")
    if args.pricing_profile != approved["pricing_profile"]:
        raise LowEffortGateError("pricing profile differs from authorization")


def command_run(args: argparse.Namespace) -> None:
    if args.target_records not in {20, 100}:
        raise LowEffortGateError("target records must be 20 or 100")
    if min(args.limit_calls, args.max_cny) <= 0:
        raise LowEffortGateError("call and budget limits must be positive")
    config = config_value()
    authorization = require_operation(AUTHORIZATION_OPERATION, CONFIG)
    _assert_authorized(args, authorization)
    pricing = pricing_record(config, args.pricing_profile, datetime.now(timezone.utc))
    if args.target_records == 100 and (
        not STAGE_A_GATE.exists()
        or load_json(STAGE_A_GATE).get("all_gates_pass") is not True
    ):
        raise LowEffortGateError("the 20-record stage-A gate has not passed")
    if not STATE.exists():
        raise LowEffortGateError("run prepare first")
    credential = config["runner"]["credential_env"]
    api_key = os.environ.get(credential) or safe_env_named_key(args.env_file, credential)
    if not api_key:
        raise LowEffortGateError(f"{credential} is not set")
    rows = benchmark_rows()
    by_index = {int(row["benchmark_item_index"]): row for row in rows}
    system_prompt = validate_contract_files()["rendered_prompt"]
    started_at = utc_now()
    with runner_lock(LOCK):
        state = load_json(STATE)
        if state.get("input_hashes") != input_hashes():
            raise LowEffortGateError("run-69 input hash drift")
        approved = authorization["approved_operation"]
        remaining_calls = int(approved["call_limit"]) - int(state["totals"]["provider_calls"])
        call_limit = min(args.limit_calls, remaining_calls)
        pending = [
            index
            for index in range(1, args.target_records + 1)
            if state["items"][str(index)]["status"] == "pending"
        ]
        size = int(config["model"]["batch_size"])
        groups = [pending[pos : pos + size] for pos in range(0, len(pending), size)][:call_limit]
        planned: list[tuple[list[int], float]] = []
        reserved = float(state["totals"]["budget_charged_cny"])
        for group in groups:
            payload = build_payload([by_index[index] for index in group])
            worst = worst_case_cost_cny(
                system_prompt,
                payload,
                config["model"],
                pricing["rates_cny_per_million"],
            )
            if reserved + worst > args.max_cny + 1e-12:
                break
            planned.append((group, worst))
            reserved += worst
        if not planned:
            raise LowEffortGateError("CNY ceiling does not permit one request or no pending records remain")
        results: list[tuple[list[int], float, dict[str, Any]]] = []
        with ThreadPoolExecutor(max_workers=int(config["model"]["workers"])) as pool:
            futures = {
                pool.submit(
                    provider_call,
                    group=[by_index[index] for index in group],
                    api_key=api_key,
                    config=config,
                    system_prompt=system_prompt,
                    pricing=pricing,
                ): (group, worst)
                for group, worst in planned
            }
            for future in as_completed(futures):
                group, worst = futures[future]
                results.append((group, worst, future.result()))
        for group, worst, result in results:
            state["totals"]["provider_calls"] += 1
            status = result.get("status")
            state["totals"]["http_402_calls"] += int(status == 402)
            state["totals"]["http_429_calls"] += int(status == 429)
            actual = result.get("actual_cost_cny")
            charge = float(actual) if actual is not None else worst
            state["totals"]["actual_cost_cny"] += float(actual or 0)
            state["totals"]["budget_charged_cny"] += charge
            usage = result.get("normalized_usage") or {}
            for key in state["totals"]["usage"]:
                state["totals"]["usage"][key] += int(usage.get(key, 0))
            profile = pricing["profile"]
            profiles = state["totals"]["pricing_profiles_used"]
            profiles[profile] = profiles.get(profile, 0) + 1
            outcomes = {int(row["item_index"]): row for row in result.get("outcomes", [])}
            for index in group:
                item = state["items"][str(index)]
                item["provider_calls"] += 1
                outcome = outcomes.get(index)
                if outcome is not None:
                    item["sealed_item_path"] = outcome["path"]
                    item["last_error"] = outcome["error"]
                    item["status"] = "complete" if outcome["valid"] else "unresolved"
                else:
                    item["last_error"] = result.get("error") or f"HTTP_{status}"
                    item["status"] = "pending" if status in {402, 429, None} else "unresolved"
        state["last_run"] = {
            "started_at": started_at,
            "completed_at": utc_now(),
            "target_records": args.target_records,
            "limit_calls": args.limit_calls,
            "max_cny": args.max_cny,
            "pricing_profile": args.pricing_profile,
            "calls_dispatched": len(results),
            "unique_records_dispatched": sum(len(group) for group, _worst, _result in results),
        }
        atomic_json(STATE, state, 0o600)
        write_manifest(state)
        print(
            json.dumps(
                {
                    "state_counts": state_counts(state),
                    "calls_this_run": len(results),
                    "actual_cost_cny_total": state["totals"]["actual_cost_cny"],
                }
            )
        )


def command_finalize(_args: argparse.Namespace) -> None:
    if not STATE.exists():
        raise LowEffortGateError("run prepare first")
    state = load_json(STATE)
    rows: list[dict[str, Any]] = []
    for index in range(1, 101):
        item = state["items"][str(index)]
        path = item.get("sealed_item_path")
        sealed = load_json(ROOT / path) if path else None
        rows.append(
            {
                "item_index": index,
                "valid": bool(sealed and sealed.get("valid")),
                "output": sealed.get("output") if sealed else None,
                "derived": sealed.get("derived") if sealed else None,
                "normalization_actions": sealed.get("normalization_actions", []) if sealed else [],
                "error": sealed.get("error") if sealed else item.get("last_error") or item["status"],
                "sealed_item_path": path,
            }
        )
    atomic_jsonl(PRIVATE_FINAL, rows, 0o600)
    write_manifest(state)
    print(json.dumps({"records": 100, "valid": sum(row["valid"] for row in rows), "api_calls": 0}))


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare")
    sub.add_parser("status")
    run = sub.add_parser("run")
    run.add_argument("--target-records", type=int, required=True)
    run.add_argument("--limit-calls", type=int, required=True)
    run.add_argument("--max-cny", type=float, required=True)
    run.add_argument("--pricing-profile", choices=("current", "new_offpeak", "new_peak"), required=True)
    run.add_argument("--env-file", type=Path, default=ROOT / ".env.ai-coding")
    sub.add_parser("finalize")
    args = parser.parse_args()
    {
        "prepare": command_prepare,
        "status": command_status,
        "run": command_run,
        "finalize": command_finalize,
    }[args.command](args)


if __name__ == "__main__":
    main()
