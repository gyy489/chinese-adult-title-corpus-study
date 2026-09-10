"""Fail-closed runner for the run-70 high-effort nested frame extension.

Only ``run`` contacts DeepSeek. Every operation requires exact central
authorization, the real price window is checked from the clock, and costs are
accounted in CNY. An external orchestrator may advance only after the versioned
continuation gate, including the mandatory 20,000-record information check.
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
from pathlib import Path
from typing import Any

import requests

from src.analysis.build_gendered_argument_visibility_budget_adaptive_20000_v1 import (
    CONFIG,
    FRAME,
    ROOT,
    RUN_DIR,
)
from src.analysis.build_gendered_argument_visibility_budget_adaptive_20000_v1 import (
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
    DEFAULT_CODEBOOK,
    DEFAULT_PROMPT,
    DEFAULT_SCHEMA,
    validate_contract_files,
    validate_output,
)
from src.analysis.provider_call_authorization_v1 import require_operation
from src.analysis.run_gendered_argument_visibility_model_benchmark_v1 import (
    parse_content,
    runner_lock,
)
from src.analysis.run_gendered_argument_visibility_pilot_v1 import validate_transport
from src.analysis.run_gendered_argument_visibility_scaleup_5000_v1 import (
    NORMALIZATION_POLICY,
    normalize_output,
)
from src.analysis.run_gendered_argument_visibility_v03_low_effort_gate_v1 import (
    actual_pricing_profile,
    safe_env_named_key,
    usage_cost_cny,
    worst_case_cost_cny,
)

EXPECTED_EXPERIMENT_ID = "70_gendered_argument_visibility_budget_adaptive_20000_v1"
STATE_DIR = RUN_DIR / "state"
STATE = STATE_DIR / "items.json"
LOCK = STATE_DIR / "runner.lock"
SEALED_CALLS = RUN_DIR / "sealed/calls"
SEALED_ITEMS = RUN_DIR / "sealed/items"
PRIVATE_FINAL = RUN_DIR / "private/final_records.jsonl"
PROGRESS_MANIFEST = RUN_DIR / "production_progress_manifest.json"
RUNNER = Path(__file__).resolve()
AUTHORIZATION_PREFIX = "run70"


class ProductionError(RuntimeError):
    """Raised before credential access when a run-70 invariant fails."""


def config_value() -> dict[str, Any]:
    value = json.loads(CONFIG.read_text(encoding="utf-8"))
    selected = value.get("measurement_policy", {}).get("selected_model_configuration")
    if (
        value.get("experiment_id") != EXPECTED_EXPERIMENT_ID
        or not isinstance(selected, dict)
        or selected.get("model") != "deepseek-v4-flash"
        or selected.get("thinking") != "enabled"
        or selected.get("reasoning_effort") != "high"
        or int(value.get("runner", {}).get("batch_size", 0)) != 4
        or int(value.get("runner", {}).get("max_output_tokens", 0)) != 64_000
        or value.get("authorization", {}).get("provider_calls_authorized") is not False
    ):
        raise ProductionError("run-70 fixed production design drifted")
    return value


def frame_rows() -> list[dict[str, Any]]:
    rows = read_jsonl(FRAME)
    config = config_value()
    expected_total = int(config["population"]["maximum_new_annotations"])
    if len(rows) != expected_total:
        raise ProductionError(f"run-70 frame must contain {expected_total:,} records")
    if [int(row["sample_item_index"]) for row in rows] != list(
        range(1, expected_total + 1)
    ):
        raise ProductionError("run-70 sample indices are not contiguous")
    prior = 0
    expected = {}
    for wave in config["sampling"]["waves"]:
        cumulative = int(wave["cumulative_records"])
        expected[str(wave["id"])] = cumulative - prior
        prior = cumulative
    if Counter(row["probability_wave"] for row in rows) != expected:
        raise ProductionError("run-70 wave counts drifted")
    return rows


def input_hashes() -> dict[str, str]:
    paths = (
        CONFIG,
        FRAME,
        FRAME_MANIFEST,
        DEFAULT_CODEBOOK,
        DEFAULT_SCHEMA,
        DEFAULT_PROMPT,
        NORMALIZATION_POLICY,
        ROOT / "src/analysis/provider_call_authorization_v1.py",
        RUNNER,
    )
    return {str(path.relative_to(ROOT)): sha256(path) for path in paths}


def state_counts(state: dict[str, Any]) -> dict[str, int]:
    return dict(sorted(Counter(row["status"] for row in state["items"].values()).items()))


def fresh_state() -> dict[str, Any]:
    return {
        "version": "1.0.0",
        "experiment_id": EXPECTED_EXPERIMENT_ID,
        "created_at": utc_now(),
        "input_hashes": input_hashes(),
        "items": {
            str(row["sample_item_index"]): {
                "sample_item_index": int(row["sample_item_index"]),
                "wave": row["probability_wave"],
                "status": "pending",
                "annotation_attempts": 0,
                "provider_calls": 0,
                "last_error": None,
                "sealed_item_path": None,
            }
            for row in frame_rows()
        },
        "totals": {
            "provider_calls": 0,
            "provider_http_calls": 0,
            "local_transport_failures": 0,
            "http_402_calls": 0,
            "http_429_calls": 0,
            "actual_cost_cny": 0.0,
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
                "item_index": int(row["sample_item_index"]),
                "deidentified_title": row["deidentified_title"],
            }
            for row in group
        ]
    }


def _json_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def pricing_record(config: dict[str, Any], requested: str) -> dict[str, Any]:
    actual = actual_pricing_profile(config, datetime.now(timezone.utc))
    if requested != actual:
        raise ProductionError(
            f"pricing clock mismatch: requested {requested}, actual {actual}"
        )
    return {
        "pricing_version": f"deepseek_official_checked_{config['pricing']['official_checked_on']}",
        "profile": actual,
        "rates_cny_per_million": config["pricing"]["profiles"][actual],
    }


def provider_call(
    *,
    group: list[dict[str, Any]],
    wave: str,
    api_key: str,
    config: dict[str, Any],
    system_prompt: str,
    pricing: dict[str, Any],
) -> dict[str, Any]:
    call_id = f"{int(time.time())}_{uuid.uuid4().hex}"
    expected = [int(row["sample_item_index"]) for row in group]
    titles = {int(row["sample_item_index"]): row["deidentified_title"] for row in group}
    payload = build_payload(group)
    started = time.time()
    record: dict[str, Any] = {
        "call_id": call_id,
        "experiment_id": EXPECTED_EXPERIMENT_ID,
        "wave": wave,
        "model": "deepseek-v4-flash",
        "thinking": "enabled",
        "reasoning_effort": "high",
        "started_at": utc_now(),
        "item_indices": expected,
        "system_prompt_sha256": hashlib.sha256(system_prompt.encode("utf-8")).hexdigest(),
        "user_payload_sha256": _json_hash(payload),
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
                "model": "deepseek-v4-flash",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                "response_format": {"type": "json_object"},
                "stream": False,
                "max_tokens": int(config["runner"]["max_output_tokens"]),
                "thinking": {"type": "enabled"},
                "reasoning_effort": "high",
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
        if record["finish_reason"] != "stop":
            raise ProductionError(f"disallowed finish reason: {record['finish_reason']}")
        transported = validate_transport(parse_content(choice["message"]["content"]), expected)
        codebook = validate_contract_files()["codebook"]
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
                "wave": wave,
                "completed_at": utc_now(),
                "valid": valid,
                "raw_output": raw,
                "output": output,
                "derived": derived,
                "normalization_actions": actions,
                "error": error,
            }
            path = SEALED_ITEMS / f"item_{index:05d}_call_{call_id}.json"
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
    value = {
        "updated_at": utc_now(),
        "status": "production_not_authorized_or_incomplete",
        "experiment_id": EXPECTED_EXPERIMENT_ID,
        "state_counts": state_counts(state),
        "totals": state["totals"],
        "last_run": state.get("last_run"),
        "inputs": input_hashes(),
        "sealed_artifacts": {
            "calls": len(calls),
            "items": len(items),
            "call_sha256": {str(path.relative_to(ROOT)): sha256(path) for path in calls},
            "item_sha256": {str(path.relative_to(ROOT)): sha256(path) for path in items},
        },
        "privacy": "No titles, record IDs, evidence spans, or provider messages are public.",
    }
    atomic_json(PROGRESS_MANIFEST, value)


def command_prepare(_args: argparse.Namespace) -> None:
    config = config_value()
    validate_contract_files()
    frame_rows()
    for path in (STATE_DIR, SEALED_CALLS, SEALED_ITEMS, PRIVATE_FINAL.parent):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.chmod(0o700)
    if STATE.exists():
        state = json.loads(STATE.read_text(encoding="utf-8"))
        if state.get("input_hashes") != input_hashes():
            expected_total = int(config["population"]["maximum_new_annotations"])
            if state["totals"]["provider_calls"] or state_counts(state) != {
                "pending": expected_total
            }:
                raise ProductionError("non-empty run-70 state belongs to different inputs")
            state = fresh_state()
            atomic_json(STATE, state, 0o600)
    else:
        state = fresh_state()
        atomic_json(STATE, state, 0o600)
    write_manifest(state)
    print(
        json.dumps(
            {
                "prepared": int(config["population"]["maximum_new_annotations"]),
                "provider_calls": 0,
            }
        )
    )


def command_status(_args: argparse.Namespace) -> None:
    if not STATE.exists():
        print(json.dumps({"status": "not_prepared"}))
        return
    state = json.loads(STATE.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "state_counts": state_counts(state),
                "totals": state["totals"],
                "last_run": state.get("last_run"),
            }
        )
    )


def _wave_ids(config: dict[str, Any]) -> list[str]:
    return [str(row["id"]) for row in config["sampling"]["waves"]]


def _budget_exhaustion_override(config: dict[str, Any]) -> bool:
    return bool(
        config.get("stopping_policy", {}).get(
            "researcher_budget_exhaustion_override"
        )
    )


def _assert_prior_gate(wave: str, config: dict[str, Any]) -> None:
    waves = _wave_ids(config)
    position = waves.index(wave)
    if position == 0:
        return
    prior = waves[position - 1]
    gate = RUN_DIR / "gates" / f"{prior}_continuation_gate_v2.json"
    if not gate.exists():
        raise ProductionError(f"prior wave audit is missing: {prior}")
    if not _budget_exhaustion_override(config) and json.loads(
        gate.read_text(encoding="utf-8")
    ).get("continuation_gate_pass") is not True:
        raise ProductionError(f"prior wave gate has not passed: {prior}")
    if prior == "probability_cap_20000":
        information = RUN_DIR / "information" / f"{prior}_information_stop.json"
        if not information.exists():
            raise ProductionError("mandatory 20,000-record information audit is missing")
        if not _budget_exhaustion_override(config) and json.loads(
            information.read_text(encoding="utf-8")
        ).get("automatic_continuation_safe") is not True:
            raise ProductionError("mandatory 20,000-record information check has not passed")


def _assert_baseline_gates() -> None:
    baseline = (
        ROOT
        / "data/interim/51_gendered_argument_visibility_scaleup_5000_v1/validation_5000_final_manifest.json"
    )
    low_gate = (
        ROOT
        / "data/interim/69_gendered_argument_visibility_v03_low_effort_gate_v1/stage_a_gate.json"
    )
    if not baseline.exists():
        raise ProductionError("frozen high-effort 5,000-record baseline is missing")
    value = json.loads(baseline.read_text(encoding="utf-8"))
    if value.get("records") != 5000 or value.get("structurally_valid") != 4998:
        raise ProductionError("frozen high-effort baseline drifted")
    if not low_gate.exists() or json.loads(low_gate.read_text(encoding="utf-8")).get("status") != "fail":
        raise ProductionError("run-69 low-effort rejection is not frozen")


def _assert_authorization(args: argparse.Namespace, authorization: dict[str, Any], state: dict[str, Any]) -> None:
    approved = authorization.get("approved_operation")
    if not isinstance(approved, dict):
        raise ProductionError("authorization limits are missing")
    if args.limit_records > int(approved["record_limit"]):
        raise ProductionError("record limit exceeds authorization")
    if int(state["totals"]["provider_calls"]) + args.limit_calls > int(approved["call_limit"]):
        raise ProductionError("cumulative call limit exceeds authorization")
    if args.max_cny > float(approved["cumulative_cny_limit"]) + 1e-12:
        raise ProductionError("CNY limit exceeds authorization")
    pricing_profiles = approved.get("pricing_profiles")
    if pricing_profiles is not None:
        if args.pricing_profile not in pricing_profiles:
            raise ProductionError("pricing profile differs from authorization")
    elif args.pricing_profile != approved["pricing_profile"]:
        raise ProductionError("pricing profile differs from authorization")
    execution_profiles = approved.get("execution_profiles")
    if execution_profiles is not None:
        if not any(
            args.workers == int(profile["workers"])
            and args.batch_size == int(profile["batch_size"])
            for profile in execution_profiles
        ):
            raise ProductionError("worker/batch profile differs from authorization")
    else:
        if args.workers != int(approved["workers"]):
            raise ProductionError("worker count differs from authorization")
        if args.batch_size != int(approved["batch_size"]):
            raise ProductionError("batch size differs from authorization")


def _assert_reduced_batch_scope(
    state: dict[str, Any], pending: list[int], batch_size: int, initial_batch_size: int = 4
) -> None:
    if batch_size < initial_batch_size and any(
        int(state["items"][str(index)]["provider_calls"]) == 0 for index in pending
    ):
        raise ProductionError("reduced batches are reserved for failed-item retries")


def command_run(args: argparse.Namespace) -> None:
    config = config_value()
    if args.wave not in _wave_ids(config):
        raise ProductionError("unknown run-70 wave")
    allowed_batches = {int(config["runner"]["batch_size"]), *map(int, config["runner"]["retry_batch_sizes"])}
    if args.batch_size not in allowed_batches:
        raise ProductionError("batch size is outside the frozen ladder")
    expected_workers = int(config["runner"]["workers_by_wave"][args.wave])
    initial_batch_size = int(config["runner"]["batch_size"])
    if args.batch_size == initial_batch_size and args.workers != expected_workers:
        raise ProductionError("initial wave worker count drifted")
    if args.batch_size < initial_batch_size and args.workers > expected_workers:
        raise ProductionError("retry workers exceed the wave maximum")
    _assert_baseline_gates()
    _assert_prior_gate(args.wave, config)
    authorization = require_operation(f"{AUTHORIZATION_PREFIX}.{args.wave}", CONFIG)
    if not STATE.exists():
        raise ProductionError("run prepare first")
    state = json.loads(STATE.read_text(encoding="utf-8"))
    if state.get("input_hashes") != input_hashes():
        raise ProductionError("run-70 input hash drift")
    _assert_authorization(args, authorization, state)
    pricing = pricing_record(config, args.pricing_profile)
    credential = config["runner"]["credential_env"]
    api_key = os.environ.get(credential) or safe_env_named_key(args.env_file, credential)
    if not api_key:
        raise ProductionError(f"{credential} is not set")
    rows = frame_rows()
    by_index = {int(row["sample_item_index"]): row for row in rows}
    system_prompt = validate_contract_files()["rendered_prompt"]
    max_attempts = int(config["measurement_policy"]["maximum_attempts_per_item"])
    max_provider_calls = int(
        config["measurement_policy"]["maximum_provider_calls_per_item"]
    )
    with runner_lock(LOCK):
        pending = [
            int(index)
            for index, item in state["items"].items()
            if item["wave"] == args.wave
            and item["status"] == "pending"
            and int(item["annotation_attempts"]) < max_attempts
            and int(item["provider_calls"]) < max_provider_calls
            and (
                int(item["provider_calls"]) == 0
                if args.batch_size == initial_batch_size
                else int(item["provider_calls"]) > 0
            )
        ][: args.limit_records]
        _assert_reduced_batch_scope(
            state, pending, args.batch_size, initial_batch_size
        )
        groups = [pending[pos : pos + args.batch_size] for pos in range(0, len(pending), args.batch_size)]
        remaining_calls = int(authorization["approved_operation"]["call_limit"]) - int(state["totals"]["provider_calls"])
        groups = groups[: min(args.limit_calls, remaining_calls)]
        planned: list[tuple[list[int], float]] = []
        reserved = float(state["totals"]["actual_cost_cny"])
        model = {"max_output_tokens": int(config["runner"]["max_output_tokens"])}
        for group in groups:
            payload = build_payload([by_index[index] for index in group])
            worst = worst_case_cost_cny(
                system_prompt, payload, model, pricing["rates_cny_per_million"]
            )
            if reserved + worst > args.max_cny + 1e-12:
                break
            planned.append((group, worst))
            reserved += worst
        if not planned:
            raise ProductionError("CNY ceiling permits no request or no pending records remain")
        started_at = utc_now()
        results: list[tuple[list[int], dict[str, Any]]] = []
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(
                    provider_call,
                    group=[by_index[index] for index in group],
                    wave=args.wave,
                    api_key=api_key,
                    config=config,
                    system_prompt=system_prompt,
                    pricing=pricing,
                ): group
                for group, _worst in planned
            }
            for future in as_completed(futures):
                results.append((futures[future], future.result()))
        stopped_402 = False
        for group, result in results:
            state["totals"]["provider_calls"] += 1
            status = result.get("status")
            state["totals"]["provider_http_calls"] += int(status is not None)
            state["totals"]["local_transport_failures"] += int(status is None)
            state["totals"]["http_402_calls"] += int(status == 402)
            state["totals"]["http_429_calls"] += int(status == 429)
            stopped_402 = stopped_402 or status == 402
            state["totals"]["actual_cost_cny"] += float(result.get("actual_cost_cny") or 0)
            usage = result.get("normalized_usage") or {}
            for key in state["totals"]["usage"]:
                state["totals"]["usage"][key] += int(usage.get(key, 0))
            profiles = state["totals"]["pricing_profiles_used"]
            profiles[pricing["profile"]] = profiles.get(pricing["profile"], 0) + 1
            outcomes = {int(row["item_index"]): row for row in result.get("outcomes", [])}
            for index in group:
                item = state["items"][str(index)]
                item["provider_calls"] += 1
                outcome = outcomes.get(index)
                if outcome is not None:
                    item["annotation_attempts"] += 1
                    item["sealed_item_path"] = outcome["path"] if outcome["valid"] else None
                    item["last_error"] = outcome["error"]
                    item["status"] = (
                        "complete"
                        if outcome["valid"]
                        else (
                            "unresolved"
                            if int(item["annotation_attempts"]) >= max_attempts
                            else "pending"
                        )
                    )
                else:
                    item["last_error"] = result.get("error") or f"HTTP_{status}"
                    item["status"] = "pending"
        state["last_run"] = {
            "started_at": started_at,
            "completed_at": utc_now(),
            "wave": args.wave,
            "workers": args.workers,
            "batch_size": args.batch_size,
            "limit_records": args.limit_records,
            "limit_calls": args.limit_calls,
            "max_cny": args.max_cny,
            "pricing_profile": args.pricing_profile,
            "calls_dispatched": len(results),
            "records_dispatched": sum(len(group) for group, _result in results),
            "stopped_402": stopped_402,
        }
        atomic_json(STATE, state, 0o600)
        write_manifest(state)
        print(
            json.dumps(
                {
                    "wave": args.wave,
                    "calls_this_run": len(results),
                    "state_counts": state_counts(state),
                    "actual_cost_cny": state["totals"]["actual_cost_cny"],
                    "stopped_402": stopped_402,
                }
            )
        )


def command_finalize(_args: argparse.Namespace) -> None:
    if not STATE.exists():
        raise ProductionError("run prepare first")
    state = json.loads(STATE.read_text(encoding="utf-8"))
    frame = {int(row["sample_item_index"]): row for row in frame_rows()}
    output: list[dict[str, Any]] = []
    total = len(frame)
    for index in range(1, total + 1):
        item = state["items"][str(index)]
        path = item.get("sealed_item_path")
        artifact = json.loads((ROOT / path).read_text(encoding="utf-8")) if path else None
        source = frame[index]
        valid = item["status"] == "complete" and artifact is not None and artifact.get("valid") is True
        output.append(
            {
                "sample_item_index": index,
                "full_item_index": int(source["full_item_index"]),
                "record_id": source["record_id"],
                "title_sha256": source["title_sha256"],
                "wave": item["wave"],
                "status": item["status"],
                "valid": valid,
                "annotation_version": "gendered_argument_visibility_v0.3.0_exact_thinking_high_run70",
                "output": artifact.get("output") if valid else None,
                "derived": artifact.get("derived") if valid else None,
                "normalization_actions": artifact.get("normalization_actions", []) if artifact else [],
                "error": None if valid else item.get("last_error"),
                "artifact_path": path,
            }
        )
    atomic_jsonl(PRIVATE_FINAL, output, 0o600)
    write_manifest(state)
    print(
        json.dumps(
            {
                "records": len(output),
                "complete": sum(row["valid"] for row in output),
                "api_calls": 0,
            }
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare")
    sub.add_parser("status")
    run = sub.add_parser("run")
    run.add_argument("--wave", required=True)
    run.add_argument("--workers", type=int, required=True)
    run.add_argument("--batch-size", type=int, required=True)
    run.add_argument("--limit-records", type=int, required=True)
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
