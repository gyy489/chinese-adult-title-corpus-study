"""Resumable v0.3 runner for the frozen 5,000-row scale-up sample."""

from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path
from typing import Any

import requests

from src.analysis import run_gendered_argument_visibility_pilot_v1 as base
from src.analysis.gendered_argument_visibility_contract_v0_3 import (
    DEFAULT_CODEBOOK,
    DEFAULT_PROMPT,
    DEFAULT_SCHEMA,
    load_json,
    validate_contract_files,
    validate_output,
)
from src.analysis.run_gendered_argument_visibility_locked_v0_2 import (
    normalize_output as normalize_v02,
)

ROOT = Path(__file__).resolve().parents[2]
RUN_DIR = ROOT / "data/interim/51_gendered_argument_visibility_scaleup_5000_v1"
PILOT = RUN_DIR / "private/sample_manifest.csv"
SAMPLE_MANIFEST = RUN_DIR / "sample_manifest.json"
CONFIG = ROOT / "config/gendered_argument_visibility_scaleup_5000_v1.json"
STATE = RUN_DIR / "state/items.json"
LOCK = RUN_DIR / "state/runner.lock"
SEALED_CALLS = RUN_DIR / "sealed/calls"
SEALED_ITEMS = RUN_DIR / "sealed/items"
PROVIDER_MANIFEST = RUN_DIR / "provider_run_manifest.json"
NORMALIZATION_POLICY = ROOT / "config/gendered_argument_visibility_normalization_v0_3_0.json"
NORMALIZER_VERSION = "argument_visibility_normalizer_v3_0"
PHASES = ("gate_0500", "discovery_2000", "validation_5000")


def normalize_output(raw: Any) -> tuple[dict[str, Any], list[str]]:
    """Reuse frozen v0.2.2 repairs; never infer the new stage-specific field."""
    output, actions = normalize_v02(raw)
    return output, list(dict.fromkeys(actions))


def chosen_indices(all_rows: list[dict[str, str]], phase: str) -> set[int]:
    allowed_waves = {
        "gate_0500": {"gate_0500"},
        "discovery_2000": {"gate_0500", "discovery_1500"},
        "validation_5000": {"gate_0500", "discovery_1500", "validation_3000"},
    }
    if phase not in allowed_waves:
        raise ValueError(f"unknown phase: {phase}")
    return {
        int(row["item_index"])
        for row in all_rows
        if row["incremental_wave"] in allowed_waves[phase]
    }


def build_provider_payload(group: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "items": [
            {
                "item_index": int(row["item_index"]),
                "deidentified_title": row["deidentified_title"],
            }
            for row in group
        ]
    }


def provider_call(
    system_prompt: str, group: list[dict[str, str]], api_key: str, phase: str
) -> dict[str, Any]:
    call_id = f"{int(time.time())}_{uuid.uuid4().hex}"
    expected = [int(row["item_index"]) for row in group]
    payload = build_provider_payload(group)
    started = time.time()
    response = requests.post(
        base.ENDPOINT,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": base.MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.0,
            "top_p": 1.0,
            "stream": False,
        },
        timeout=600,
    )
    response.raise_for_status()
    body = response.json()
    choice = body["choices"][0]
    message = choice["message"]
    call_record = {
        "call_id": call_id,
        "phase": phase,
        "completed_at": base.utc_now(),
        "model": base.MODEL,
        "elapsed_s": time.time() - started,
        "usage": body.get("usage", {}),
        "item_indices": expected,
        "finish_reason": choice.get("finish_reason"),
        "provider_message": message,
    }
    base.atomic_json(SEALED_CALLS / f"call_{call_id}.json", call_record)
    parsed = base.parse_content(message["content"])
    transported = base.validate_transport(parsed, expected)
    codebook = load_json(DEFAULT_CODEBOOK)
    titles = {int(row["item_index"]): row["deidentified_title"] for row in group}
    outcomes: list[dict[str, Any]] = []
    for index, raw in transported:
        try:
            output, actions = normalize_output(raw)
            derived = validate_output(output, titles[index], codebook)
            valid = True
            error = None
        except Exception as exc:  # noqa: BLE001
            output = None
            actions = []
            derived = None
            valid = False
            error = f"{type(exc).__name__}:{exc}"
        item = {
            "item_index": index,
            "call_id": call_id,
            "phase": phase,
            "completed_at": base.utc_now(),
            "valid": valid,
            "raw_output": raw,
            "output": output,
            "derived": derived,
            "normalization_actions": actions,
            "error": error,
        }
        path = SEALED_ITEMS / f"item_{index:06d}_call_{call_id}.json"
        base.atomic_json(path, item)
        outcomes.append(
            {
                "item_index": index,
                "valid": valid,
                "error": error,
                "path": str(path.relative_to(ROOT)),
            }
        )
    return {
        "call_id": call_id,
        "outcomes": outcomes,
        "usage": call_record["usage"],
        "elapsed_s": call_record["elapsed_s"],
    }


def configure_base() -> None:
    config = load_json(CONFIG)
    base.RUN_DIR = RUN_DIR
    base.PILOT = PILOT
    base.SAMPLE_MANIFEST = SAMPLE_MANIFEST
    base.CONFIG = CONFIG
    base.STATE = STATE
    base.LOCK = LOCK
    base.SEALED_CALLS = SEALED_CALLS
    base.SEALED_ITEMS = SEALED_ITEMS
    base.PROVIDER_MANIFEST = PROVIDER_MANIFEST
    base.NORMALIZATION_POLICY = NORMALIZATION_POLICY
    base.NORMALIZER_VERSION = NORMALIZER_VERSION
    base.DEFAULT_CODEBOOK = DEFAULT_CODEBOOK
    base.DEFAULT_SCHEMA = DEFAULT_SCHEMA
    base.DEFAULT_PROMPT = DEFAULT_PROMPT
    base.MODEL = config["model"]
    base.normalize_output = normalize_output
    base.validate_output = validate_output
    base.validate_contract_files = validate_contract_files
    base.chosen_indices = chosen_indices
    base.provider_call = provider_call


def command_prepare(_args: argparse.Namespace) -> None:
    contract = validate_contract_files()
    all_rows = base.rows()
    if len(all_rows) != 5000:
        raise RuntimeError(f"frozen sample contains {len(all_rows)} rows, expected 5000")
    state = {
        row["item_index"]: {
            "item_index": int(row["item_index"]),
            "pilot_phase": row["incremental_wave"],
            "sampling_track": row["sampling_track"],
            "sampling_stratum": row["sampling_stratum"],
            "status": "pending",
            "attempts": 0,
            "last_error": None,
            "sealed_item_path": None,
        }
        for row in all_rows
    }
    base.atomic_json(STATE, state)
    config = load_json(CONFIG)
    manifest = {
        "created_at": base.utc_now(),
        "status": "prepared_not_run",
        "model": base.MODEL,
        "endpoint": base.ENDPOINT,
        "records": len(all_rows),
        "contract_version": contract["codebook"]["version"],
        "inference_parameters": {
            "temperature": 0.0,
            "top_p": 1.0,
            "stream": False,
            "provider_seed": "not_supported_or_not_set",
        },
        "inputs": {
            str(path.relative_to(ROOT)): base.sha256(path)
            for path in (
                PILOT,
                SAMPLE_MANIFEST,
                CONFIG,
                DEFAULT_CODEBOOK,
                DEFAULT_SCHEMA,
                DEFAULT_PROMPT,
                NORMALIZATION_POLICY,
            )
        },
        "phase_policy": {
            "gate_0500": {"cumulative_records": 500, "incremental_records": 500},
            "discovery_2000": {"cumulative_records": 2000, "incremental_records": 1500},
            "validation_5000": {"cumulative_records": 5000, "incremental_records": 3000},
            "batch_size": int(config["batch_size"]),
            "workers": int(config["workers"]),
            "max_attempts_per_item": int(config["max_attempts_per_item"]),
        },
        "pricing_usd_per_million": {"input": base.PRICE_INPUT, "output": base.PRICE_OUTPUT},
        "normalizer_version": NORMALIZER_VERSION,
        "payload_policy": "Only item_index and deidentified_title are included in the user payload.",
        "credential_status": "set/not-set is checked at run time; the value is never logged",
    }
    base.atomic_json(PROVIDER_MANIFEST, manifest, 0o644)
    print(json.dumps({"prepared": len(state), "contract": "v0.3.0"}, ensure_ascii=False))


def main() -> None:
    configure_base()
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare")
    sub.add_parser("status")
    run = sub.add_parser("run")
    run.add_argument("--phase", choices=PHASES, required=True)
    run.add_argument("--workers", type=int, required=True)
    run.add_argument("--batch-size", type=int, required=True)
    run.add_argument("--max-attempts", type=int, default=1)
    run.add_argument("--env-file", type=Path, default=ROOT / ".env.ai-coding")
    finalize = sub.add_parser("finalize")
    finalize.add_argument("--phase", choices=PHASES, required=True)
    finalize.add_argument("--attempt-label", default="")
    args = parser.parse_args()
    {
        "prepare": command_prepare,
        "status": base.command_status,
        "run": base.command_run,
        "finalize": base.command_finalize,
    }[args.command](args)


if __name__ == "__main__":
    main()
