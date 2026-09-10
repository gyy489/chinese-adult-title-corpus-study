"""Fail-closed, resumable runner for run 58's exact-v0.3 smoke test.

Only ``run`` contacts DeepSeek.  ``prepare``, ``status``, and ``finalize`` are
offline.  The frozen v0.3 codebook, schema, prompt, and normalizer are reused;
the sole model-side change is ``thinking=disabled``.
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
from urllib.parse import urlsplit, urlunsplit

import requests
from requests.adapters import HTTPAdapter

from src.analysis.build_gendered_argument_visibility_model_benchmark_v1 import (
    atomic_json,
    atomic_jsonl,
    read_jsonl,
    sha256,
    utc_now,
)
from src.analysis.build_gendered_argument_visibility_v03_nonthinking_equivalence_v1 import (
    CONFIG,
    PRIVATE_ITEMS,
    ROOT,
    RUN_DIR,
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
    parse_content,
    pricing_record,
    runner_lock,
    usage_cost_usd,
    worst_case_cost_usd,
)
from src.analysis.run_gendered_argument_visibility_pilot_v1 import validate_transport
from src.analysis.run_gendered_argument_visibility_scaleup_5000_v1 import (
    NORMALIZATION_POLICY,
    normalize_output,
)

DEFAULT_NORMALIZATION_POLICY = NORMALIZATION_POLICY
DEFAULT_NORMALIZE_OUTPUT = normalize_output

STATE_DIR = RUN_DIR / "state"
STATE = STATE_DIR / "items.json"
LOCK = STATE_DIR / "runner.lock"
SEALED_CALLS = RUN_DIR / "sealed/calls"
SEALED_ITEMS = RUN_DIR / "sealed/items"
PRIVATE_FINAL = RUN_DIR / "private/final_records.jsonl"
PROVIDER_MANIFEST = RUN_DIR / "provider_run_manifest.json"
AUTHORIZATION_OPERATION = "run58.v03_nonthinking_smoke_40"
AUTHORIZATION_DEPENDENCY = ROOT / "src/analysis/provider_call_authorization_v1.py"
RUNNER = ROOT / "src/analysis/run_gendered_argument_visibility_v03_nonthinking_equivalence_v1.py"
ENGINE_DEPENDENCY = Path(__file__).resolve()
NORMALIZER_DEPENDENCY: Path | None = None


class PinnedDNSHTTPSAdapter(HTTPAdapter):
    """Connect to a resolved IP while retaining TLS SNI and hostname checks."""

    def __init__(self, hostname: str, address: str) -> None:
        self.hostname = hostname
        self.address = address
        super().__init__()

    def init_poolmanager(
        self,
        connections: int,
        maxsize: int,
        block: bool = False,
        **pool_kwargs: Any,
    ) -> None:
        pool_kwargs["assert_hostname"] = self.hostname
        pool_kwargs["server_hostname"] = self.hostname
        super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)

    def send(self, request: requests.PreparedRequest, **kwargs: Any) -> requests.Response:
        parsed = urlsplit(request.url)
        port = f":{parsed.port}" if parsed.port else ""
        request.url = urlunsplit(
            (
                parsed.scheme,
                f"{self.address}{port}",
                parsed.path,
                parsed.query,
                parsed.fragment,
            )
        )
        request.headers["Host"] = self.hostname
        return super().send(request, **kwargs)


def safe_env_named_key(path: Path, variable: str) -> str:
    """Read only the explicitly requested credential from a dotenv file."""
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() != variable:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        return value
    return ""


def config_value() -> dict[str, Any]:
    value = load_json(CONFIG)
    model = value["model"]
    if (
        value.get("records") != 100
        or value.get("smoke_records") != 40
        or model.get("model") != "deepseek-v4-flash"
        or model.get("thinking") != "disabled"
        or model.get("batch_size") != 5
        or model.get("workers") != 8
    ):
        raise RuntimeError("run-58 fixed design drifted")
    return value


def benchmark_rows() -> list[dict[str, Any]]:
    rows = read_jsonl(PRIVATE_ITEMS)
    if len(rows) != 100:
        raise RuntimeError("run 58 requires exactly 100 frozen records")
    if [int(row["benchmark_item_index"]) for row in rows] != list(range(1, 101)):
        raise RuntimeError("run-58 indices must be contiguous 1..100")
    if any(row.get("frozen_split") == "final_blind_test" for row in rows):
        raise RuntimeError("run 58 contains protected final-blind data")
    return rows


def input_hashes() -> dict[str, str]:
    paths = [
        PRIVATE_ITEMS,
        CONFIG,
        DEFAULT_CODEBOOK,
        DEFAULT_SCHEMA,
        DEFAULT_PROMPT,
        NORMALIZATION_POLICY,
        AUTHORIZATION_DEPENDENCY,
        RUNNER,
    ]
    if ENGINE_DEPENDENCY not in paths:
        paths.append(ENGINE_DEPENDENCY)
    if NORMALIZER_DEPENDENCY is not None and NORMALIZER_DEPENDENCY not in paths:
        paths.append(NORMALIZER_DEPENDENCY)
    return {
        str(path.relative_to(ROOT)): sha256(path)
        for path in paths
    }


def json_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def state_counts(state: dict[str, Any]) -> dict[str, int]:
    return dict(sorted(Counter(item["status"] for item in state["items"].values()).items()))


def fresh_state(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": config["version"],
        "experiment_id": config["experiment_id"],
        "created_at": utc_now(),
        "input_hashes": input_hashes(),
        "items": {
            str(row["benchmark_item_index"]): {
                "item_index": int(row["benchmark_item_index"]),
                "partition": row["partition"],
                "benchmark_stratum": row["benchmark_stratum"],
                "status": "pending",
                "provider_calls": 0,
                "last_error": None,
                "sealed_item_path": None,
            }
            for row in benchmark_rows()
        },
        "totals": {
            "provider_calls": 0,
            "http_402_calls": 0,
            "http_429_calls": 0,
            "actual_cost_usd": 0.0,
            "budget_charged_usd": 0.0,
            "usage": {
                "cache_hit_input_tokens": 0,
                "cache_miss_input_tokens": 0,
                "output_tokens": 0,
            },
            "pricing_profiles_used": {},
            "pricing_effective_used": {},
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
        "experiment_id": config["experiment_id"],
        "model": config["model"]["model"],
        "thinking": config["model"]["thinking"],
        "started_at": utc_now(),
        "item_indices": expected,
        "system_prompt_sha256": hashlib.sha256(system_prompt.encode("utf-8")).hexdigest(),
        "user_payload_sha256": json_sha256(payload),
        "pricing": pricing,
        "http_status": None,
        "usage": None,
        "normalized_usage": None,
        "actual_cost_usd": None,
        "finish_reason": None,
        "provider_message": None,
        "error": None,
        "transport": {"mode": "system_dns"},
    }
    response: requests.Response | None = None
    try:
        request_body: dict[str, Any] = {
            "model": config["model"]["model"],
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
            "temperature": float(config["runner"]["temperature"]),
            "top_p": float(config["runner"]["top_p"]),
            "stream": bool(config["runner"]["stream"]),
        }
        if not config["runner"].get("omit_max_tokens_when_structured_output", False):
            request_body["max_tokens"] = int(config["model"]["max_output_tokens"])
        thinking_parameter = config["runner"].get("thinking_parameter", "deepseek")
        if thinking_parameter == "deepseek":
            request_body["thinking"] = {"type": config["model"]["thinking"]}
        elif thinking_parameter == "dashscope_enable_thinking":
            request_body["enable_thinking"] = config["model"]["thinking"] == "enabled"
            if config["runner"].get("thinking_budget") is not None:
                request_body["thinking_budget"] = int(config["runner"]["thinking_budget"])
        else:
            raise RuntimeError(f"unsupported thinking parameter: {thinking_parameter}")
        transport = config["runner"].get("transport")
        request_kwargs = {
            "headers": {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            "json": request_body,
            "timeout": int(config["runner"]["request_timeout_seconds"]),
        }
        if isinstance(transport, dict) and transport.get("mode") == "pinned_dns_fallback":
            hostname = str(transport["hostname"])
            addresses = [str(value) for value in transport["addresses"]]
            if not addresses:
                raise RuntimeError("pinned DNS fallback has no addresses")
            address = addresses[(expected[0] // int(config["model"]["batch_size"])) % len(addresses)]
            session = requests.Session()
            session.trust_env = False
            session.mount(
                f"https://{hostname}/",
                PinnedDNSHTTPSAdapter(hostname, address),
            )
            record["transport"] = {
                "mode": "pinned_dns_fallback",
                "hostname": hostname,
                "address": address,
                "resolution_provenance": transport["resolution_provenance"],
            }
            with session:
                response = session.post(config["runner"]["endpoint"], **request_kwargs)
        else:
            response = requests.post(config["runner"]["endpoint"], **request_kwargs)
        record["http_status"] = response.status_code
        if response.status_code != 200:
            record["error"] = f"HTTP_{response.status_code}"
            return {"kind": "http_error", "status": response.status_code, "call_id": call_id}
        body = response.json()
        usage = body.get("usage", {})
        cost, normalized = usage_cost_usd(usage, pricing["rates_usd_per_million"])
        record["usage"] = usage
        record["normalized_usage"] = normalized
        record["actual_cost_usd"] = cost
        choice = body["choices"][0]
        record["finish_reason"] = choice.get("finish_reason")
        record["provider_message"] = choice["message"]
        parsed = parse_content(choice["message"]["content"])
        transported = validate_transport(parsed, expected)
        codebook = validate_contract_files()["codebook"]
        title_by_index = {
            int(row["benchmark_item_index"]): row["deidentified_title"] for row in group
        }
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
                "completed_at": utc_now(),
                "valid": valid,
                "raw_output": raw,
                "output": output,
                "derived": derived,
                "normalization_actions": actions,
                "error": error,
            }
            path = SEALED_ITEMS / f"item_{index:03d}_call_{call_id}.json"
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
            "status": 200,
            "call_id": call_id,
            "outcomes": outcomes,
            "actual_cost_usd": float(cost or 0.0),
            "normalized_usage": normalized,
        }
    except Exception as exc:  # noqa: BLE001
        record["error"] = f"{type(exc).__name__}:{exc}"
        return {
            "kind": "batch_error",
            "status": response.status_code if response is not None else None,
            "call_id": call_id,
            "error": record["error"],
            "actual_cost_usd": float(record.get("actual_cost_usd") or 0.0),
            "normalized_usage": record.get("normalized_usage"),
        }
    finally:
        record["completed_at"] = utc_now()
        record["elapsed_s"] = time.time() - started
        atomic_json(SEALED_CALLS / f"call_{call_id}.json", record, 0o600)


def write_manifest(config: dict[str, Any], state: dict[str, Any]) -> None:
    calls = sorted(SEALED_CALLS.glob("call_*.json"))
    items = sorted(SEALED_ITEMS.glob("item_*.json"))
    manifest: dict[str, Any] = {
        "updated_at": utc_now(),
        "status": "complete" if state_counts(state).get("complete", 0) == 100 else "incomplete",
        "experiment_id": config["experiment_id"],
        "records": 100,
        "smoke_records": 40,
        "model": config["model"]["model"],
        "thinking": config["model"]["thinking"],
        "state_counts": state_counts(state),
        "provider_calls": state["totals"]["provider_calls"],
        "http_402_calls": state["totals"]["http_402_calls"],
        "http_429_calls": state["totals"]["http_429_calls"],
        "actual_cost_usd": state["totals"]["actual_cost_usd"],
        "budget_charged_usd": state["totals"]["budget_charged_usd"],
        "usage": state["totals"]["usage"],
        "pricing_profiles_used": state["totals"]["pricing_profiles_used"],
        "pricing_effective_used": state["totals"]["pricing_effective_used"],
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
    if PRIVATE_FINAL.exists():
        manifest["private_final_sha256"] = sha256(PRIVATE_FINAL)
    atomic_json(PROVIDER_MANIFEST, manifest)


def assert_authorized_limits(authorization: dict[str, Any], args: argparse.Namespace) -> None:
    approved = authorization.get("approved_operation")
    if not isinstance(approved, dict):
        raise TypeError("authorization does not expose limits")
    if args.limit_records > int(approved["record_limit"]):
        raise RuntimeError("record limit exceeds authorization")
    if args.limit_calls > int(approved["call_limit"]):
        raise RuntimeError("call limit exceeds authorization")
    if args.max_usd > float(approved["cumulative_usd_limit"]) + 1e-12:
        raise RuntimeError("USD limit exceeds authorization")
    if args.pricing_profile != approved.get("pricing_profile"):
        raise RuntimeError("pricing profile differs from authorization")


def command_prepare(_args: argparse.Namespace) -> None:
    config = config_value()
    validate_contract_files()
    benchmark_rows()
    for path in (STATE_DIR, SEALED_CALLS, SEALED_ITEMS):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.chmod(0o700)
    if STATE.exists():
        state = load_json(STATE)
        if state.get("input_hashes") != input_hashes():
            if state["totals"].get("provider_calls", 0) or state_counts(state) != {"pending": 100}:
                raise RuntimeError("non-empty run-58 state belongs to different inputs")
            state = fresh_state(config)
            atomic_json(STATE, state, 0o600)
    else:
        state = fresh_state(config)
        atomic_json(STATE, state, 0o600)
    write_manifest(config, state)
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
                "actual_cost_usd": state["totals"]["actual_cost_usd"],
                "last_run": state.get("last_run"),
            }
        )
    )


def command_run(args: argparse.Namespace) -> None:
    if min(args.limit_records, args.limit_calls) <= 0 or args.max_usd <= 0:
        raise ValueError("record, call, and USD limits must be positive")
    config = config_value()
    authorization = require_operation(AUTHORIZATION_OPERATION, CONFIG)
    assert_authorized_limits(authorization, args)
    if not STATE.exists():
        raise RuntimeError("run prepare first")
    credential_env = config["runner"].get("credential_env", "DEEPSEEK_API_KEY")
    api_key = os.environ.get(credential_env) or safe_env_named_key(args.env_file, credential_env)
    if not api_key:
        raise RuntimeError(f"{credential_env} is not set")
    system_prompt = validate_contract_files()["rendered_prompt"]
    rows = benchmark_rows()
    row_by_index = {int(row["benchmark_item_index"]): row for row in rows}
    model = config["model"]
    started_at = utc_now()
    with runner_lock(LOCK):
        state = load_json(STATE)
        if state.get("input_hashes") != input_hashes():
            raise RuntimeError("run-58 input hash drift")
        approved = authorization["approved_operation"]
        remaining_calls = int(approved["call_limit"]) - int(state["totals"]["provider_calls"])
        call_limit = min(args.limit_calls, remaining_calls)
        if call_limit <= 0:
            raise RuntimeError("authorized cumulative call limit is exhausted")
        eligible = [
            int(index)
            for index, item in state["items"].items()
            if int(index) <= int(approved["record_limit"]) and item["status"] == "pending"
        ][: args.limit_records]
        groups = [eligible[pos : pos + int(model["batch_size"])] for pos in range(0, len(eligible), int(model["batch_size"]))]
        groups = groups[:call_limit]
        pricing = pricing_record(
            config,
            model["model"],
            datetime.now(timezone.utc),
            args.pricing_profile,
        )
        planned: list[list[int]] = []
        reserved = float(state["totals"]["budget_charged_usd"])
        for group in groups:
            payload = build_payload([row_by_index[index] for index in group])
            upper = worst_case_cost_usd(system_prompt, payload, model, pricing["rates_usd_per_million"])
            if reserved + upper > args.max_usd + 1e-12:
                break
            planned.append(group)
            reserved += upper
        if not planned:
            raise RuntimeError("USD ceiling does not permit one request")
        records_dispatched: set[int] = set()
        results: list[tuple[list[int], dict[str, Any]]] = []
        with ThreadPoolExecutor(max_workers=int(model["workers"])) as pool:
            futures = {}
            for group in planned:
                records_dispatched.update(group)
                future = pool.submit(
                    provider_call,
                    group=[row_by_index[index] for index in group],
                    api_key=api_key,
                    config=config,
                    system_prompt=system_prompt,
                    pricing=pricing,
                )
                futures[future] = group
            for future in as_completed(futures):
                results.append((futures[future], future.result()))

        stopped_402 = False
        for group, result in results:
            state["totals"]["provider_calls"] += 1
            status = result.get("status")
            if status == 402:
                state["totals"]["http_402_calls"] += 1
                stopped_402 = True
            if status == 429:
                state["totals"]["http_429_calls"] += 1
            actual = float(result.get("actual_cost_usd") or 0.0)
            state["totals"]["actual_cost_usd"] += actual
            state["totals"]["budget_charged_usd"] += actual
            normalized = result.get("normalized_usage") or {}
            for key in state["totals"]["usage"]:
                state["totals"]["usage"][key] += int(normalized.get(key, 0))
            profile = pricing["profile"]
            state["totals"]["pricing_profiles_used"][profile] = state["totals"]["pricing_profiles_used"].get(profile, 0) + 1
            state["totals"]["pricing_effective_used"][profile] = pricing["effective"]
            outcomes = {int(item["item_index"]): item for item in result.get("outcomes", [])}
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
                    item["status"] = "pending" if status != 200 else "unresolved"
        state["last_run"] = {
            "started_at": started_at,
            "completed_at": utc_now(),
            "limit_records": args.limit_records,
            "limit_calls": args.limit_calls,
            "max_usd": args.max_usd,
            "pricing_profile": args.pricing_profile,
            "calls_dispatched": len(results),
            "unique_records_dispatched": len(records_dispatched),
            "stopped_402": stopped_402,
        }
        atomic_json(STATE, state, 0o600)
        write_manifest(config, state)
        print(
            json.dumps(
                {
                    "state_counts": state_counts(state),
                    "calls_this_run": len(results),
                    "actual_cost_usd_total": state["totals"]["actual_cost_usd"],
                }
            )
        )


def command_finalize(_args: argparse.Namespace) -> None:
    config = config_value()
    state = load_json(STATE)
    rows: list[dict[str, Any]] = []
    for index in range(1, 101):
        item = state["items"][str(index)]
        sealed_path = item.get("sealed_item_path")
        if sealed_path:
            sealed = load_json(ROOT / sealed_path)
            rows.append(
                {
                    "item_index": index,
                    "valid": bool(sealed.get("valid")),
                    "output": sealed.get("output"),
                    "derived": sealed.get("derived"),
                    "normalization_actions": sealed.get("normalization_actions", []),
                    "error": sealed.get("error"),
                    "sealed_item_path": sealed_path,
                }
            )
        else:
            rows.append(
                {
                    "item_index": index,
                    "valid": False,
                    "output": None,
                    "derived": None,
                    "normalization_actions": [],
                    "error": item.get("last_error") or item["status"],
                    "sealed_item_path": None,
                }
            )
    atomic_jsonl(PRIVATE_FINAL, rows, 0o600)
    write_manifest(config, state)
    print(json.dumps({"records": 100, "valid": sum(row["valid"] for row in rows), "api_calls": 0}))


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare")
    sub.add_parser("status")
    run = sub.add_parser("run")
    run.add_argument("--limit-records", type=int, required=True)
    run.add_argument("--limit-calls", type=int, required=True)
    run.add_argument("--max-usd", type=float, required=True)
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
