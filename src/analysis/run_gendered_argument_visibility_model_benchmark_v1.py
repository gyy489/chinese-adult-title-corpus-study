"""Safe, resumable runner for the 52nd low-cost DeepSeek benchmark.

Each model configuration has isolated state and sealed provider artifacts.  No
API call is made by ``prepare``, ``status``, or ``finalize``.  ``run`` requires
an explicit USD ceiling and submits batches lazily, so at most ``workers``
requests are in flight.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import threading
import time
import uuid
from collections import Counter, deque
from collections.abc import Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import contextmanager
from datetime import datetime, timezone
from datetime import time as datetime_time
from pathlib import Path
from typing import Any

import requests

from src.analysis.build_gendered_argument_visibility_model_benchmark_v1 import (
    CONFIG,
    PRIVATE_ITEMS,
    ROOT,
    RUN_DIR,
    atomic_json,
    atomic_jsonl,
    read_jsonl,
    sha256,
    utc_now,
)
from src.analysis.gendered_argument_visibility_benchmark_contract_v1 import (
    ContractError,
    load_json,
    validate_compact_item,
    validate_contract_files,
)

STATE_ROOT = RUN_DIR / "state"
SEALED_ROOT = RUN_DIR / "sealed"
PRIVATE_FINAL_ROOT = RUN_DIR / "private/final"
_stop = threading.Event()


def model_config(config: dict[str, Any], config_id: str) -> dict[str, Any]:
    matches = [entry for entry in config["model_configs"] if entry["id"] == config_id]
    if len(matches) != 1:
        raise ValueError(f"unknown or duplicate config id: {config_id}")
    if not re.fullmatch(r"[a-z0-9_]+", config_id):
        raise ValueError("config id contains unsafe characters")
    return matches[0]


def config_paths(config_id: str) -> dict[str, Path]:
    state_dir = STATE_ROOT / config_id
    sealed_dir = SEALED_ROOT / config_id
    return {
        "state_dir": state_dir,
        "state": state_dir / "items.json",
        "lock": state_dir / "runner.lock",
        "calls": sealed_dir / "calls",
        "items": sealed_dir / "items",
        "provider_manifest": RUN_DIR / f"provider_run_manifest_{config_id}.json",
        "final": PRIVATE_FINAL_ROOT / f"{config_id}_final_records.jsonl",
    }


def safe_env_key(path: Path) -> str:
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() != "DEEPSEEK_API_KEY":
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        return value
    return ""


def benchmark_rows() -> list[dict[str, Any]]:
    rows = read_jsonl(PRIVATE_ITEMS)
    if len(rows) != 100:
        raise RuntimeError("benchmark must be built with exactly 100 records")
    if [row["benchmark_item_index"] for row in rows] != list(range(1, 101)):
        raise RuntimeError("benchmark indices must be contiguous 1..100")
    return rows


def parse_content(content: str) -> Any:
    value = content.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value)
        value = re.sub(r"\s*```$", "", value)
    return json.loads(value)


def build_user_payload(group: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "items": [
            {"i": int(row["benchmark_item_index"]), "t": row["deidentified_title"]}
            for row in group
        ]
    }


def resolve_pricing_profile(
    config: dict[str, Any], at: datetime, override: str | None = None
) -> str:
    profiles = config["pricing"]["profiles"]
    if override is not None:
        if override not in profiles:
            raise ValueError(f"unknown pricing profile: {override}")
        return override
    transition = datetime.fromisoformat(config["pricing"]["transition_utc"])
    if at.astimezone(timezone.utc) < transition:
        return "current"
    utc_time = at.astimezone(timezone.utc).time().replace(tzinfo=None)
    for start, end in config["pricing"]["new_peak_windows_utc"]:
        start_time = datetime_time.fromisoformat(start)
        end_time = datetime_time.fromisoformat(end)
        if start_time <= utc_time < end_time:
            return "new_peak"
    return "new_offpeak"


def pricing_record(
    config: dict[str, Any], model: str, at: datetime, override: str | None
) -> dict[str, Any]:
    profile = resolve_pricing_profile(config, at, override)
    profile_value = config["pricing"]["profiles"][profile]
    return {
        "pricing_version": config["pricing"]["pricing_version"],
        "profile": profile,
        "effective": profile_value["effective"],
        "override": override,
        "rates_usd_per_million": profile_value[model],
    }


def normalized_usage(usage: Any) -> dict[str, int] | None:
    if not isinstance(usage, dict):
        return None
    prompt = int(usage.get("prompt_tokens", 0) or 0)
    hit = usage.get("prompt_cache_hit_tokens")
    if hit is None:
        details = usage.get("prompt_tokens_details") or {}
        hit = details.get("cached_tokens", 0) if isinstance(details, dict) else 0
    hit = int(hit or 0)
    miss = usage.get("prompt_cache_miss_tokens")
    miss = int(miss if miss is not None else max(0, prompt - hit))
    output = int(usage.get("completion_tokens", 0) or 0)
    if min(hit, miss, output) < 0:
        return None
    return {
        "cache_hit_input_tokens": hit,
        "cache_miss_input_tokens": miss,
        "output_tokens": output,
        "prompt_tokens": prompt,
        "total_tokens": int(usage.get("total_tokens", prompt + output) or 0),
    }


def usage_cost_usd(usage: Any, rates: dict[str, float]) -> tuple[float | None, dict[str, int] | None]:
    normalized = normalized_usage(usage)
    if normalized is None:
        return None, None
    cost = (
        normalized["cache_hit_input_tokens"] * float(rates["cache_hit_input"])
        + normalized["cache_miss_input_tokens"] * float(rates["cache_miss_input"])
        + normalized["output_tokens"] * float(rates["output"])
    ) / 1_000_000
    return cost, normalized


def worst_case_cost_usd(
    system_prompt: str,
    user_payload: dict[str, Any],
    model_entry: dict[str, Any],
    rates: dict[str, float],
) -> float:
    request_bytes = len(system_prompt.encode("utf-8")) + len(
        json.dumps(user_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    input_upper_bound = request_bytes + 512
    return (
        input_upper_bound * float(rates["cache_miss_input"])
        + int(model_entry["max_output_tokens"]) * float(rates["output"])
    ) / 1_000_000


def _transport_items(value: Any, expected: set[int]) -> list[dict[str, Any]]:
    if not isinstance(value, dict) or set(value) != {"items"}:
        raise ContractError("response must contain exactly top-level items")
    items = value["items"]
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ContractError("items must be an array of objects")
    indices = [item.get("i") for item in items]
    if len(indices) != len(set(indices)) or set(indices) != expected:
        raise ContractError("response index set differs from request")
    return sorted(items, key=lambda item: item["i"])


def provider_call(
    *,
    call_id: str,
    group: list[dict[str, Any]],
    api_key: str,
    config: dict[str, Any],
    model_entry: dict[str, Any],
    system_prompt: str,
    pricing: dict[str, Any],
    paths: dict[str, Path],
) -> dict[str, Any]:
    started = time.time()
    expected = {int(row["benchmark_item_index"]) for row in group}
    user_payload = build_user_payload(group)
    call_record: dict[str, Any] = {
        "call_id": call_id,
        "config_id": model_entry["id"],
        "model": model_entry["model"],
        "thinking": model_entry["thinking"],
        "started_at": utc_now(),
        "item_indices": sorted(expected),
        "pricing": pricing,
        "http_status": None,
        "usage": None,
        "normalized_usage": None,
        "actual_cost_usd": None,
        "provider_message": None,
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
                "model": model_entry["model"],
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": json.dumps(user_payload, ensure_ascii=False, separators=(",", ":")),
                    },
                ],
                "response_format": {"type": "json_object"},
                "temperature": float(config["runner"]["temperature"]),
                "top_p": float(config["runner"]["top_p"]),
                "stream": bool(config["runner"]["stream"]),
                "max_tokens": int(model_entry["max_output_tokens"]),
                "thinking": {"type": model_entry["thinking"]},
            },
            timeout=int(config["runner"]["request_timeout_seconds"]),
        )
        call_record["http_status"] = response.status_code
        if response.status_code != 200:
            call_record["error"] = f"HTTP_{response.status_code}"
            retry_after = response.headers.get("Retry-After")
            return {
                "kind": "http_error",
                "http_status": response.status_code,
                "retry_after": retry_after,
                "outcomes": [],
                "call_id": call_id,
            }
        body = response.json()
        usage = body.get("usage", {})
        call_record["usage"] = usage
        cost, normalized = usage_cost_usd(
            usage, pricing["rates_usd_per_million"]
        )
        call_record["normalized_usage"] = normalized
        call_record["actual_cost_usd"] = cost
        choice = body["choices"][0]
        message = choice["message"]
        call_record["finish_reason"] = choice.get("finish_reason")
        call_record["provider_message"] = message
        parsed = parse_content(message["content"])
        raw_items = _transport_items(parsed, expected)
        title_by_index = {
            int(row["benchmark_item_index"]): row["deidentified_title"] for row in group
        }
        codebook = validate_contract_files()["codebook"]
        for raw_item in raw_items:
            index = int(raw_item["i"])
            try:
                validation = validate_compact_item(
                    raw_item, title_by_index[index], codebook
                )
                valid = True
                error = None
                expanded = validation["expanded"]
            except Exception as exc:  # noqa: BLE001
                valid = False
                error = f"{type(exc).__name__}:{exc}"
                expanded = None
            item_record = {
                "call_id": call_id,
                "config_id": model_entry["id"],
                "completed_at": utc_now(),
                "item_index": index,
                "valid": valid,
                "raw_compact_output": raw_item,
                "expanded_output": expanded,
                "error": error,
            }
            item_path = paths["items"] / f"item_{index:03d}_call_{call_id}.json"
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
            "normalized_usage": normalized,
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
        atomic_json(paths["calls"] / f"call_{call_id}.json", call_record, 0o600)


def fresh_state(config: dict[str, Any], model_entry: dict[str, Any]) -> dict[str, Any]:
    rows = benchmark_rows()
    return {
        "version": config["version"],
        "config_id": model_entry["id"],
        "model": model_entry["model"],
        "thinking": model_entry["thinking"],
        "created_at": utc_now(),
        "benchmark_items_sha256": sha256(PRIVATE_ITEMS),
        "items": {
            str(row["benchmark_item_index"]): {
                "item_index": row["benchmark_item_index"],
                "partition": row["partition"],
                "benchmark_stratum": row["benchmark_stratum"],
                "status": "pending",
                "annotation_attempts": 0,
                "provider_calls": 0,
                "last_error": None,
                "sealed_item_path": None,
                "active_call_id": None,
            }
            for row in rows
        },
        "totals": {
            "provider_calls": 0,
            "http_429_calls": 0,
            "http_402_calls": 0,
            "actual_cost_usd": 0.0,
            "budget_charged_usd": 0.0,
            "reserved_usd": 0.0,
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


def load_state(path: Path) -> dict[str, Any]:
    return load_json(path)


def save_state(path: Path, state: dict[str, Any]) -> None:
    atomic_json(path, state, 0o600)


def recover_interrupted(state: dict[str, Any]) -> None:
    reserved = float(state["totals"].get("reserved_usd", 0.0))
    if reserved:
        state["totals"]["budget_charged_usd"] += reserved
        state["totals"]["reserved_usd"] = 0.0
    for item in state["items"].values():
        if item["status"] == "in_flight":
            item["status"] = "pending"
            item["last_error"] = "interrupted_in_flight_conservatively_budgeted"
            item["active_call_id"] = None


@contextmanager
def runner_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise RuntimeError(f"runner lock exists: {path}") from exc
    try:
        os.write(descriptor, f"pid={os.getpid()} started={utc_now()}\n".encode())
        os.close(descriptor)
        yield
    finally:
        path.unlink(missing_ok=True)


def state_counts(state: dict[str, Any]) -> dict[str, int]:
    return dict(sorted(Counter(item["status"] for item in state["items"].values()).items()))


def write_provider_manifest(
    config: dict[str, Any], model_entry: dict[str, Any], state: dict[str, Any], path: Path
) -> None:
    manifest = {
        "updated_at": utc_now(),
        "status": "complete" if state_counts(state).get("complete", 0) == 100 else "incomplete",
        "config_id": model_entry["id"],
        "model": model_entry["model"],
        "thinking": model_entry["thinking"],
        "records": 100,
        "state_counts": state_counts(state),
        "provider_calls": state["totals"]["provider_calls"],
        "http_429_calls": state["totals"]["http_429_calls"],
        "http_402_calls": state["totals"]["http_402_calls"],
        "actual_cost_usd": state["totals"]["actual_cost_usd"],
        "budget_charged_usd": state["totals"]["budget_charged_usd"],
        "usage": state["totals"]["usage"],
        "pricing_version": config["pricing"]["pricing_version"],
        "pricing_profiles_used": state["totals"].get("pricing_profiles_used", {}),
        "pricing_effective_used": state["totals"].get("pricing_effective_used", {}),
        "pricing_schedule": {
            "transition_utc": config["pricing"]["transition_utc"],
            "new_peak_windows_utc": config["pricing"]["new_peak_windows_utc"],
        },
        "last_run": state.get("last_run"),
        "privacy": "No titles, record IDs, evidence spans, or provider text are public.",
    }
    atomic_json(path, manifest)


def command_prepare(args: argparse.Namespace) -> None:
    config = load_json(CONFIG)
    entry = model_config(config, args.config_id)
    paths = config_paths(args.config_id)
    validate_contract_files()
    benchmark_rows()
    for key in ("state_dir", "calls", "items"):
        paths[key].mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(paths[key], 0o700)
    if paths["state"].exists():
        state = load_state(paths["state"])
        if state.get("benchmark_items_sha256") != sha256(PRIVATE_ITEMS):
            counts = state_counts(state)
            sealed_calls = list(paths["calls"].glob("call_*.json"))
            sealed_items = list(paths["items"].glob("item_*.json"))
            if (
                int(state["totals"].get("provider_calls", 0)) != 0
                or float(state["totals"].get("actual_cost_usd", 0.0)) != 0.0
                or counts != {"pending": 100}
                or sealed_calls
                or sealed_items
            ):
                raise RuntimeError(
                    "existing non-empty state belongs to a different benchmark sample"
                )
            state = fresh_state(config, entry)
            save_state(paths["state"], state)
            prepared = True
        else:
            prepared = False
    else:
        state = fresh_state(config, entry)
        save_state(paths["state"], state)
        prepared = True
    write_provider_manifest(config, entry, state, paths["provider_manifest"])
    print(json.dumps({"config_id": args.config_id, "prepared": prepared, "api_calls": 0}))


def command_status(args: argparse.Namespace) -> None:
    config = load_json(CONFIG)
    model_config(config, args.config_id)
    paths = config_paths(args.config_id)
    if not paths["state"].exists():
        print(json.dumps({"config_id": args.config_id, "status": "not_prepared"}))
        return
    state = load_state(paths["state"])
    print(
        json.dumps(
            {
                "config_id": args.config_id,
                "state_counts": state_counts(state),
                "provider_calls": state["totals"]["provider_calls"],
                "actual_cost_usd": state["totals"]["actual_cost_usd"],
                "budget_charged_usd": state["totals"]["budget_charged_usd"],
                "last_run": state.get("last_run"),
            }
        )
    )


def _groups(rows: list[dict[str, Any]], batch_size: int) -> deque[list[dict[str, Any]]]:
    return deque(rows[offset : offset + batch_size] for offset in range(0, len(rows), batch_size))


def _retry_delay(config: dict[str, Any], retry_number: int, retry_after: Any) -> float:
    schedule = config["runner"]["http_429_backoff_seconds"]
    scheduled = float(schedule[min(retry_number, len(schedule) - 1)])
    try:
        provider = float(retry_after)
    except (TypeError, ValueError):
        provider = 0.0
    return min(60.0, max(scheduled, provider))


def command_run(args: argparse.Namespace) -> None:
    if args.max_usd <= 0 or args.limit_records <= 0 or args.limit_calls <= 0:
        raise ValueError("max-usd, limit-records, and limit-calls must be positive")
    config = load_json(CONFIG)
    entry = model_config(config, args.config_id)
    paths = config_paths(args.config_id)
    if not paths["state"].exists():
        raise RuntimeError("run prepare first")
    workers = args.workers if args.workers is not None else int(entry["workers_default"])
    if not 1 <= workers <= int(config["runner"]["max_workers"]):
        raise ValueError(f"workers must be 1..{config['runner']['max_workers']}")
    batch_size = args.batch_size if args.batch_size is not None else int(entry["batch_size"])
    if not 1 <= batch_size <= 25:
        raise ValueError("batch-size must be 1..25")
    api_key = os.environ.get("DEEPSEEK_API_KEY") or safe_env_key(args.env_file)
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not set")
    contract = validate_contract_files()
    system_prompt = contract["rendered_prompt"]
    all_rows = benchmark_rows()
    by_index = {row["benchmark_item_index"]: row for row in all_rows}
    _stop.clear()

    def stop_handler(_signum: int, _frame: Any) -> None:
        _stop.set()

    previous_int = signal.signal(signal.SIGINT, stop_handler)
    previous_term = signal.signal(signal.SIGTERM, stop_handler)
    started_at = utc_now()
    try:
        with runner_lock(paths["lock"]):
            state = load_state(paths["state"])
            recover_interrupted(state)
            max_attempts = int(config["runner"]["max_attempts_per_item"])
            pending_indices = [
                int(index)
                for index, item in sorted(state["items"].items(), key=lambda pair: int(pair[0]))
                if item["status"] == "pending"
                and int(item["annotation_attempts"]) < max_attempts
            ][: args.limit_records]
            queue = _groups([by_index[index] for index in pending_indices], batch_size)
            futures: dict[Future[dict[str, Any]], dict[str, Any]] = {}
            calls_this_run = 0
            records_dispatched: set[int] = set()
            fused_402 = False
            budget_blocked = False
            retry_number = 0
            save_state(paths["state"], state)
            with ThreadPoolExecutor(max_workers=workers) as executor:
                while queue or futures:
                    while (
                        queue
                        and len(futures) < workers
                        and calls_this_run < args.limit_calls
                        and not _stop.is_set()
                        and not fused_402
                    ):
                        raw_group = queue.popleft()
                        group = [
                            row
                            for row in raw_group
                            if state["items"][str(row["benchmark_item_index"])]["status"]
                            == "pending"
                        ]
                        if not group:
                            continue
                        now = datetime.now(timezone.utc)
                        pricing = pricing_record(
                            config, entry["model"], now, args.pricing_profile
                        )
                        worst = worst_case_cost_usd(
                            system_prompt,
                            build_user_payload(group),
                            entry,
                            pricing["rates_usd_per_million"],
                        )
                        budget_total = (
                            float(state["totals"]["budget_charged_usd"])
                            + float(state["totals"]["reserved_usd"])
                            + worst
                        )
                        if budget_total > args.max_usd + 1e-12:
                            queue.appendleft(group)
                            budget_blocked = True
                            break
                        call_id = f"{int(time.time())}_{uuid.uuid4().hex}"
                        state["totals"]["provider_calls"] += 1
                        state["totals"]["reserved_usd"] += worst
                        profiles_used = state["totals"].setdefault(
                            "pricing_profiles_used", {}
                        )
                        profile = pricing["profile"]
                        profiles_used[profile] = int(profiles_used.get(profile, 0)) + 1
                        state["totals"].setdefault("pricing_effective_used", {})[
                            profile
                        ] = pricing["effective"]
                        for row in group:
                            index = str(row["benchmark_item_index"])
                            item = state["items"][index]
                            item["status"] = "in_flight"
                            item["provider_calls"] += 1
                            item["active_call_id"] = call_id
                            records_dispatched.add(int(index))
                        save_state(paths["state"], state)
                        future = executor.submit(
                            provider_call,
                            call_id=call_id,
                            group=group,
                            api_key=api_key,
                            config=config,
                            model_entry=entry,
                            system_prompt=system_prompt,
                            pricing=pricing,
                            paths=paths,
                        )
                        futures[future] = {
                            "group": group,
                            "worst": worst,
                            "pricing": pricing,
                        }
                        calls_this_run += 1
                    if not futures:
                        break
                    done, _ = wait(set(futures), return_when=FIRST_COMPLETED)
                    for future in done:
                        metadata = futures.pop(future)
                        group = metadata["group"]
                        try:
                            result = future.result()
                        except Exception as exc:  # noqa: BLE001  # pragma: no cover
                            result = {
                                "kind": "batch_error",
                                "http_status": None,
                                "outcomes": [],
                                "error": f"{type(exc).__name__}:{exc}",
                            }
                        state["totals"]["reserved_usd"] = max(
                            0.0,
                            float(state["totals"]["reserved_usd"])
                            - float(metadata["worst"]),
                        )
                        actual = result.get("actual_cost_usd")
                        charge = float(actual) if actual is not None else float(metadata["worst"])
                        if actual is not None:
                            state["totals"]["actual_cost_usd"] += float(actual)
                        state["totals"]["budget_charged_usd"] += charge
                        usage = result.get("normalized_usage")
                        if isinstance(usage, dict):
                            for key in state["totals"]["usage"]:
                                state["totals"]["usage"][key] += int(usage.get(key, 0))
                        kind = result["kind"]
                        status = result.get("http_status")
                        if status == 429:
                            state["totals"]["http_429_calls"] += 1
                        if status == 402:
                            state["totals"]["http_402_calls"] += 1
                            fused_402 = True
                        outcomes = {outcome["item_index"]: outcome for outcome in result["outcomes"]}
                        for row in group:
                            index = int(row["benchmark_item_index"])
                            item = state["items"][str(index)]
                            item["active_call_id"] = None
                            if kind == "success":
                                item["annotation_attempts"] += 1
                                outcome = outcomes[index]
                                item["last_error"] = outcome["error"]
                                if outcome["valid"]:
                                    item["status"] = "complete"
                                    item["sealed_item_path"] = outcome["path"]
                                elif item["annotation_attempts"] >= max_attempts:
                                    item["status"] = "unresolved"
                                else:
                                    item["status"] = "pending"
                            elif status == 402:
                                item["status"] = "pending"
                                item["last_error"] = "HTTP_402_fuse"
                            elif status == 429:
                                item["status"] = "pending"
                                item["last_error"] = "HTTP_429_backoff"
                            else:
                                if status == 200:
                                    item["annotation_attempts"] += 1
                                item["last_error"] = result.get("error") or f"HTTP_{status}"
                                item["status"] = (
                                    "unresolved"
                                    if item["annotation_attempts"] >= max_attempts
                                    else "pending"
                                )
                        if (
                            status == 429
                            and calls_this_run < args.limit_calls
                            and not _stop.is_set()
                            and not fused_402
                        ):
                            queue.append(group)
                            delay = _retry_delay(
                                config, retry_number, result.get("retry_after")
                            )
                            retry_number += 1
                            time.sleep(delay)
                        save_state(paths["state"], state)
                    if budget_blocked and not futures:
                        break
            state["last_run"] = {
                "started_at": started_at,
                "completed_at": utc_now(),
                "workers": workers,
                "batch_size": batch_size,
                "limit_records": args.limit_records,
                "limit_calls": args.limit_calls,
                "max_usd": args.max_usd,
                "pricing_profile_override": args.pricing_profile,
                "calls_dispatched": calls_this_run,
                "unique_records_dispatched": len(records_dispatched),
                "stopped_402": fused_402,
                "budget_blocked": budget_blocked,
                "signal_stop": _stop.is_set(),
            }
            save_state(paths["state"], state)
            write_provider_manifest(config, entry, state, paths["provider_manifest"])
            print(
                json.dumps(
                    {
                        "config_id": args.config_id,
                        "state_counts": state_counts(state),
                        "calls_this_run": calls_this_run,
                        "actual_cost_usd_total": state["totals"]["actual_cost_usd"],
                        "budget_charged_usd_total": state["totals"]["budget_charged_usd"],
                        "stopped_402": fused_402,
                        "budget_blocked": budget_blocked,
                    }
                )
            )
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)


def command_finalize(args: argparse.Namespace) -> None:
    config = load_json(CONFIG)
    entry = model_config(config, args.config_id)
    paths = config_paths(args.config_id)
    if not paths["state"].exists():
        raise RuntimeError("run prepare first")
    state = load_state(paths["state"])
    rows: list[dict[str, Any]] = []
    for index, item in sorted(state["items"].items(), key=lambda pair: int(pair[0])):
        if item["status"] != "complete" or not item["sealed_item_path"]:
            continue
        sealed = load_json(ROOT / item["sealed_item_path"])
        rows.append(
            {
                "item_index": int(index),
                "config_id": args.config_id,
                "valid": sealed["valid"],
                "compact_output": sealed["raw_compact_output"],
                "expanded_output": sealed["expanded_output"],
                "sealed_item_path": item["sealed_item_path"],
            }
        )
    atomic_jsonl(paths["final"], rows, 0o600)
    write_provider_manifest(config, entry, state, paths["provider_manifest"])
    print(
        json.dumps(
            {
                "config_id": args.config_id,
                "finalized_records": len(rows),
                "complete": len(rows) == 100,
                "api_calls": 0,
            }
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("prepare", "status", "finalize"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--config-id", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--config-id", required=True)
    run.add_argument("--workers", type=int)
    run.add_argument("--batch-size", type=int)
    run.add_argument("--limit-records", type=int, default=100)
    run.add_argument("--limit-calls", type=int, default=20)
    run.add_argument("--max-usd", type=float, required=True)
    run.add_argument(
        "--pricing-profile", choices=("current", "new_offpeak", "new_peak")
    )
    run.add_argument("--env-file", type=Path, default=ROOT / ".env.ai-coding")
    args = parser.parse_args()
    {
        "prepare": command_prepare,
        "status": command_status,
        "run": command_run,
        "finalize": command_finalize,
    }[args.command](args)


if __name__ == "__main__":
    main()
