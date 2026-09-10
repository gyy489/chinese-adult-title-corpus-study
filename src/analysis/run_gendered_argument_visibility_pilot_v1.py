"""Resumable DeepSeek runner for argument-visibility smoke/development phases."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import signal
import tempfile
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from src.analysis.gendered_argument_visibility_contract_v0_1 import (
    DEFAULT_CODEBOOK,
    DEFAULT_PROMPT,
    DEFAULT_SCHEMA,
    POSITION_EVIDENCE_FIELDS,
    load_json,
    render_system_prompt,
    validate_contract_files,
    validate_output,
)

ROOT = Path(__file__).resolve().parents[2]
RUN_DIR = ROOT / "data/interim/48_gendered_argument_visibility_pilot_v1"
PILOT = RUN_DIR / "private/pilot_manifest.csv"
SAMPLE_MANIFEST = RUN_DIR / "sample_manifest.json"
CONFIG = ROOT / "config/gendered_argument_visibility_pilot_v1.json"
STATE = RUN_DIR / "state/items.json"
LOCK = RUN_DIR / "state/runner.lock"
SEALED_CALLS = RUN_DIR / "sealed/calls"
SEALED_ITEMS = RUN_DIR / "sealed/items"
PROVIDER_MANIFEST = RUN_DIR / "provider_run_manifest.json"
NORMALIZATION_POLICY = (
    ROOT / "config/gendered_argument_visibility_normalization_v0_1_4.json"
)
MODEL = "deepseek-v4-flash"
ENDPOINT = "https://api.deepseek.com/chat/completions"
PRICE_INPUT = 0.14
PRICE_OUTPUT = 0.28
NORMALIZER_VERSION = "argument_visibility_normalizer_v1_4"
_stop = threading.Event()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_json(path: Path, value: Any, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        temp = Path(f.name)
    os.chmod(temp, mode)
    os.replace(temp, path)


def atomic_text(path: Path, value: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as f:
        f.write(value)
        temp = Path(f.name)
    os.chmod(temp, mode)
    os.replace(temp, path)


def rows() -> list[dict[str, str]]:
    with PILOT.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def safe_env_key(path: Path) -> str:
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() == "DEEPSEEK_API_KEY":
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            return value
    return ""


def build_prompt() -> str:
    codebook = load_json(DEFAULT_CODEBOOK)
    return render_system_prompt(codebook, DEFAULT_PROMPT.read_text(encoding="utf-8"))


def parse_content(content: str) -> Any:
    value = content.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value)
        value = re.sub(r"\s*```$", "", value)
    return json.loads(value)


def normalize_support(support: Any) -> dict[str, str]:
    if isinstance(support, dict) and set(support) == {"field", "code"}:
        return support
    if isinstance(support, str):
        for separator in (":", "=", "."):
            if separator in support:
                field, code = support.split(separator, 1)
                return {"field": field.strip(), "code": code.strip()}
    if isinstance(support, list) and len(support) == 2:
        return {"field": str(support[0]), "code": str(support[1])}
    raise ValueError("unsupported evidence support representation")


def normalize_output(raw: Any) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(raw, dict):
        raise TypeError("output must be object")
    value = json.loads(json.dumps(raw, ensure_ascii=False))
    actions: list[str] = []
    for field in (
        "visible_gender_positions",
        "control_form",
        "attributed_speech_act",
        "editorial_framing_cues",
        "context_frame_codes",
        "adjudication_reasons",
    ):
        if field in value and isinstance(value[field], str):
            value[field] = [] if not value[field].strip() else [value[field]]
            actions.append(f"scalar_to_array:{field}")
    context_codes = value.get("context_frame_codes")
    if isinstance(context_codes, list) and "deception_or_incapacity_explicit" in context_codes:
        value["context_frame_codes"] = [
            "incapacity_or_deception_explicit"
            if code == "deception_or_incapacity_explicit"
            else code
            for code in context_codes
        ]
        actions.append("enum_alias:deception_or_incapacity_explicit")
    if "adjudication_reason" in value and "adjudication_reasons" not in value:
        reason = value.pop("adjudication_reason")
        value["adjudication_reasons"] = (
            [reason] if isinstance(reason, str) and reason.strip() else []
        )
        actions.append("field_alias:adjudication_reason")
    if "derived_diagnostics" in value:
        value.pop("derived_diagnostics")
        actions.append("remove_extra:derived_diagnostics")
    for prefix in ("sexual_action", "control"):
        visibility = value.get(f"{prefix}_visibility")
        if visibility == "absent":
            for role in ("actor", "target"):
                field = f"{prefix}_{role}_position"
                if value.get(field) == "not_applicable":
                    value[field] = "not_visible"
                    actions.append(f"absent_event_position:{field}")
            if prefix == "control" and value.get("control_form") == ["not_applicable"]:
                value["control_form"] = ["none_visible"]
                actions.append("absent_control_form:none_visible")
        if visibility in {"explicit", "implied"}:
            actor_position = f"{prefix}_actor_position"
            actor_realization = f"{prefix}_actor_realization"
            if (
                value.get(actor_position) == "not_visible"
                and value.get(actor_realization) == "not_applicable"
            ):
                value[actor_realization] = "event_visible_actor_not_visible"
                actions.append(f"unexpressed_actor:{actor_realization}")
            position = f"{prefix}_target_position"
            realization = f"{prefix}_target_realization"
            if (
                value.get(position) == "not_visible"
                and value.get(realization) == "not_applicable"
            ):
                value[realization] = "object_omitted"
                actions.append(f"omitted_target:{realization}")
    evidence = value.get("evidence")
    if isinstance(evidence, list):
        retained_evidence = []
        for item in evidence:
            if isinstance(item, dict) and isinstance(item.get("supports"), list):
                normalized = [normalize_support(s) for s in item["supports"]]
                if normalized != item["supports"]:
                    actions.append("normalize:evidence_supports")
                for support in normalized:
                    if (
                        support.get("field") == "context_frame_codes"
                        and support.get("code")
                        == "deception_or_incapacity_explicit"
                    ):
                        support["code"] = "incapacity_or_deception_explicit"
                        actions.append("enum_alias:evidence_context_frame_codes")
                filtered = [
                    support
                    for support in normalized
                    if support.get("field") != "record_validity"
                    and support.get("code") != "no_visible_authorization_language"
                ]
                if filtered != normalized:
                    actions.append("drop_evidence_support:non_evidence_field_or_sentinel")
                item["supports"] = filtered
                if not filtered:
                    actions.append("drop_empty_evidence_row")
                    continue
            retained_evidence.append(item)
        value["evidence"] = retained_evidence
        if value.get("person_reference_status") == "visible":
            person_pair = {"field": "person_reference_status", "code": "visible"}
            has_person_support = any(
                person_pair in item.get("supports", [])
                for item in retained_evidence
                if isinstance(item, dict)
            )
            if not has_person_support:
                specific_fields = {
                    "participant_configuration",
                    "visible_gender_positions",
                    "person_focus_position",
                }
                donor = next(
                    (
                        item
                        for item in retained_evidence
                        if isinstance(item, dict)
                        and any(
                            support.get("field") in specific_fields
                            for support in item.get("supports", [])
                            if isinstance(support, dict)
                        )
                    ),
                    None,
                )
                if donor is not None:
                    donor["supports"].append(person_pair)
                    actions.append("inherit_evidence:person_reference_status=visible")
        configured = value.get("participant_configuration")
        configuration_pair = {
            "field": "participant_configuration",
            "code": configured,
        }
        has_configuration_support = any(
            configuration_pair in item.get("supports", [])
            for item in retained_evidence
            if isinstance(item, dict)
        )
        if configured == "single" and not has_configuration_support:
            donor = next(
                (
                    item
                    for item in retained_evidence
                    if isinstance(item, dict)
                    and any(
                        support.get("field")
                        in {
                            "person_reference_status",
                            "visible_gender_positions",
                            "person_focus_position",
                        }
                        for support in item.get("supports", [])
                        if isinstance(support, dict)
                    )
                ),
                None,
            )
            if donor is not None:
                donor["supports"].append(configuration_pair)
                actions.append("inherit_evidence:participant_configuration=single")
        for gender_code in value.get("visible_gender_positions", []):
            gender_pair = {"field": "visible_gender_positions", "code": gender_code}
            has_gender_support = any(
                gender_pair in item.get("supports", [])
                for item in retained_evidence
                if isinstance(item, dict)
            )
            if not has_gender_support:
                donor = next(
                    (
                        item
                        for item in retained_evidence
                        if isinstance(item, dict)
                        and any(
                            support.get("code") == gender_code
                            and support.get("field") in POSITION_EVIDENCE_FIELDS
                            for support in item.get("supports", [])
                            if isinstance(support, dict)
                        )
                    ),
                    None,
                )
                if donor is not None:
                    donor["supports"].append(gender_pair)
                    actions.append(f"inherit_evidence:visible_gender_positions={gender_code}")
        for prefix in ("sexual_action", "control"):
            actor_realization_field = f"{prefix}_actor_realization"
            actor_pair = {
                "field": actor_realization_field,
                "code": "event_visible_actor_not_visible",
            }
            has_actor_support = any(
                actor_pair in item.get("supports", [])
                for item in retained_evidence
                if isinstance(item, dict)
            )
            if (
                value.get(actor_realization_field)
                == "event_visible_actor_not_visible"
                and not has_actor_support
            ):
                event_pair = {
                    "field": f"{prefix}_visibility",
                    "code": value.get(f"{prefix}_visibility"),
                }
                donor = next(
                    (
                        item
                        for item in retained_evidence
                        if isinstance(item, dict)
                        and event_pair in item.get("supports", [])
                    ),
                    None,
                )
                if donor is not None:
                    donor["supports"].append(actor_pair)
                    actions.append(
                        f"inherit_evidence:{actor_realization_field}="
                        "event_visible_actor_not_visible"
                    )
            realization_field = f"{prefix}_target_realization"
            omitted_pair = {"field": realization_field, "code": "object_omitted"}
            has_omitted_support = any(
                omitted_pair in item.get("supports", [])
                for item in retained_evidence
                if isinstance(item, dict)
            )
            if value.get(realization_field) == "object_omitted" and not has_omitted_support:
                event_pair = {
                    "field": f"{prefix}_visibility",
                    "code": value.get(f"{prefix}_visibility"),
                }
                donor = next(
                    (
                        item
                        for item in retained_evidence
                        if isinstance(item, dict)
                        and event_pair in item.get("supports", [])
                    ),
                    None,
                )
                if donor is not None:
                    donor["supports"].append(omitted_pair)
                    actions.append(f"inherit_evidence:{realization_field}=object_omitted")
            explicit_pair = {
                "field": realization_field,
                "code": "explicit_person_or_role",
            }
            has_explicit_support = any(
                explicit_pair in item.get("supports", [])
                for item in retained_evidence
                if isinstance(item, dict)
            )
            if (
                value.get(realization_field) == "explicit_person_or_role"
                and not has_explicit_support
            ):
                target_position_pair = {
                    "field": f"{prefix}_target_position",
                    "code": value.get(f"{prefix}_target_position"),
                }
                donor = next(
                    (
                        item
                        for item in retained_evidence
                        if isinstance(item, dict)
                        and target_position_pair in item.get("supports", [])
                    ),
                    None,
                )
                if donor is not None:
                    donor["supports"].append(explicit_pair)
                    actions.append(f"inherit_evidence:{realization_field}=explicit_person_or_role")
        configuration_pair = {"field": "participant_configuration", "code": "dyad"}
        has_configuration_support = any(
            configuration_pair in item.get("supports", [])
            for item in retained_evidence
            if isinstance(item, dict)
        )
        positions = value.get("visible_gender_positions", [])
        if (
            value.get("participant_configuration") == "dyad"
            and isinstance(positions, list)
            and len(set(positions)) >= 2
            and not has_configuration_support
        ):
            donors = [
                item
                for item in retained_evidence
                if isinstance(item, dict)
                and any(
                    support.get("field") == "visible_gender_positions"
                    for support in item.get("supports", [])
                    if isinstance(support, dict)
                )
            ]
            if len(donors) >= 2:
                for donor in donors:
                    donor["supports"].append(configuration_pair)
                actions.append("inherit_evidence:participant_configuration=dyad")
        if value.get("privacy_chain_relevance") == "relevant":
            relevance_pair = {"field": "privacy_chain_relevance", "code": "relevant"}
            has_relevance_support = any(
                relevance_pair in item.get("supports", [])
                for item in retained_evidence
                if isinstance(item, dict)
            )
            if not has_relevance_support:
                privacy_fields = {
                    "material_creation_visibility",
                    "acquisition_visibility",
                    "distribution_visibility",
                    "exposure_visibility",
                    "distribution_authorization_visibility",
                }
                donor = next(
                    (
                        item
                        for item in retained_evidence
                        if isinstance(item, dict)
                        and any(
                            support.get("field") in privacy_fields
                            for support in item.get("supports", [])
                            if isinstance(support, dict)
                        )
                    ),
                    None,
                )
                if donor is not None:
                    donor["supports"].append(relevance_pair)
                    actions.append("inherit_evidence:privacy_chain_relevance=relevant")
    return value, list(dict.fromkeys(actions))


def validate_transport(parsed: Any, expected: list[int]) -> list[tuple[int, Any]]:
    if not isinstance(parsed, dict) or set(parsed) != {"results"}:
        raise ValueError("response must contain exactly results")
    results = parsed["results"]
    if not isinstance(results, list) or len(results) != len(expected):
        raise ValueError("response count mismatch")
    by_index: dict[int, Any] = {}
    for item in results:
        if not isinstance(item, dict) or set(item) != {"item_index", "output"}:
            raise ValueError("transport item must contain item_index/output")
        index = item["item_index"]
        if not isinstance(index, int) or index in by_index:
            raise ValueError("duplicate/noninteger item_index")
        by_index[index] = item["output"]
    if set(by_index) != set(expected):
        raise ValueError("item_index set mismatch")
    return [(i, by_index[i]) for i in expected]


def provider_call(
    system_prompt: str, group: list[dict[str, str]], api_key: str, phase: str
) -> dict[str, Any]:
    call_id = f"{int(time.time())}_{uuid.uuid4().hex}"
    expected = [int(r["item_index"]) for r in group]
    payload = {
        "task": "annotate_gendered_argument_visibility",
        "batch_id": f"{phase}_{call_id}",
        "items": [
            {"item_index": int(r["item_index"]), "title": r["deidentified_title"]}
            for r in group
        ],
    }
    started = time.time()
    response = requests.post(
        ENDPOINT,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": MODEL,
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
    content = message["content"]
    call_record = {
        "call_id": call_id,
        "phase": phase,
        "completed_at": utc_now(),
        "model": MODEL,
        "elapsed_s": time.time() - started,
        "usage": body.get("usage", {}),
        "item_indices": expected,
        "finish_reason": choice.get("finish_reason"),
        "provider_message": message,
    }
    atomic_json(SEALED_CALLS / f"call_{call_id}.json", call_record)
    parsed = parse_content(content)
    transported = validate_transport(parsed, expected)
    codebook = load_json(DEFAULT_CODEBOOK)
    titles = {int(r["item_index"]): r["deidentified_title"] for r in group}
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
            "completed_at": utc_now(),
            "valid": valid,
            "raw_output": raw,
            "output": output,
            "derived": derived,
            "normalization_actions": actions,
            "error": error,
        }
        path = SEALED_ITEMS / f"item_{index:06d}_attempt_01.json"
        if path.exists():
            raise RuntimeError(f"sealed item exists: {path}")
        atomic_json(path, item)
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


def cost(usage: dict[str, Any]) -> float:
    return (
        int(usage.get("prompt_tokens", 0) or 0) / 1e6 * PRICE_INPUT
        + int(usage.get("completion_tokens", 0) or 0) / 1e6 * PRICE_OUTPUT
    )


def chosen_indices(all_rows: list[dict[str, str]], phase: str) -> set[int]:
    development = [r for r in all_rows if r["pilot_phase"] == "development"]
    if phase == "development":
        return {int(r["item_index"]) for r in development}
    selected: set[int] = set()
    for stratum in sorted({r["sampling_stratum"] for r in development}):
        bucket = sorted(
            (r for r in development if r["sampling_stratum"] == stratum),
            key=lambda r: int(r["item_index"]),
        )
        selected.update(int(r["item_index"]) for r in bucket[:2])
    return selected


def command_prepare(_args: argparse.Namespace) -> None:
    validate_contract_files()
    all_rows = rows()
    state = {
        r["item_index"]: {
            "item_index": int(r["item_index"]),
            "pilot_phase": r["pilot_phase"],
            "sampling_stratum": r["sampling_stratum"],
            "status": "pending",
            "attempts": 0,
            "last_error": None,
            "sealed_item_path": None,
        }
        for r in all_rows
    }
    atomic_json(STATE, state)
    config = load_json(CONFIG)
    manifest = {
        "created_at": utc_now(),
        "status": "prepared_not_run",
        "model": MODEL,
        "endpoint": ENDPOINT,
        "total_records": len(all_rows),
        "inference_parameters": {
            "temperature": 0.0,
            "top_p": 1.0,
            "stream": False,
            "provider_seed": "not_supported_or_not_set",
        },
        "inputs": {
            str(p.relative_to(ROOT)): sha256(p)
            for p in (
                PILOT,
                SAMPLE_MANIFEST,
                CONFIG,
                DEFAULT_CODEBOOK,
                DEFAULT_SCHEMA,
                DEFAULT_PROMPT,
            )
        },
        "phase_policy": {
            "smoke": {
                "records": 12,
                "batch_size": 2,
                "workers": config["smoke_workers"],
                "max_attempts": 1,
            },
            "development": {
                "records": 120,
                "batch_size": 10,
                "workers": config["development_workers"],
                "max_attempts": 1,
            },
        },
        "pricing_usd_per_million": {"input": PRICE_INPUT, "output": PRICE_OUTPUT},
        "normalizer_version": NORMALIZER_VERSION,
        "payload_policy": "Only batch_id, item_index and deidentified title are sent.",
        "credential_status": "set/not-set is checked at run time; credential value is never logged",
    }
    atomic_json(PROVIDER_MANIFEST, manifest, 0o644)
    print(json.dumps({"prepared": len(state)}, ensure_ascii=False))


def command_status(_args: argparse.Namespace) -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    counts: Counter[str] = Counter(v["status"] for v in state.values())
    print(
        json.dumps(
            {"status": dict(counts), "lock": LOCK.exists()},
            ensure_ascii=False,
            indent=2,
        )
    )


def command_run(args: argparse.Namespace) -> None:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "") or safe_env_key(args.env_file)
    if not api_key:
        raise SystemExit("DEEPSEEK_API_KEY is not set")
    if LOCK.exists():
        raise SystemExit("runner lock exists")
    atomic_json(
        LOCK, {"pid": os.getpid(), "started_at": utc_now(), "phase": args.phase}
    )
    signal.signal(signal.SIGINT, lambda *_: _stop.set())
    signal.signal(signal.SIGTERM, lambda *_: _stop.set())
    try:
        all_rows = rows()
        by_index = {int(r["item_index"]): r for r in all_rows}
        selected = chosen_indices(all_rows, args.phase)
        state = json.loads(STATE.read_text(encoding="utf-8"))
        pending = [
            v
            for v in state.values()
            if v["item_index"] in selected
            and v["status"] != "completed"
            and v["attempts"] < args.max_attempts
        ]
        pending.sort(key=lambda v: v["item_index"])
        groups = [
            [by_index[v["item_index"]] for v in pending[i : i + args.batch_size]]
            for i in range(0, len(pending), args.batch_size)
        ]
        print(
            f"{args.phase}: {len(pending)} records, {len(groups)} calls, {args.workers} workers"
        )
        system_prompt = build_prompt()
        usage = Counter()
        elapsed = []
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(
                    provider_call, system_prompt, group, api_key, args.phase
                ): group
                for group in groups
            }
            for future in as_completed(futures):
                group = futures[future]
                try:
                    result = future.result()
                    elapsed.append(result["elapsed_s"])
                    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                        usage[key] += int(result["usage"].get(key, 0) or 0)
                    by_outcome = {o["item_index"]: o for o in result["outcomes"]}
                    for row in group:
                        entry = state[row["item_index"]]
                        entry["attempts"] += 1
                        outcome = by_outcome[int(row["item_index"])]
                        entry["last_error"] = outcome["error"]
                        entry["sealed_item_path"] = outcome["path"]
                        entry["status"] = (
                            "completed" if outcome["valid"] else "unresolved"
                        )
                except Exception as exc:  # noqa: BLE001
                    for row in group:
                        entry = state[row["item_index"]]
                        entry["last_error"] = f"call_error:{type(exc).__name__}:{exc}"[
                            :500
                        ]
                        entry["status"] = "pending"
                atomic_json(STATE, state)
        selected_state = [v for v in state.values() if v["item_index"] in selected]
        summary = {
            "phase": args.phase,
            "selected": len(selected_state),
            "completed": sum(v["status"] == "completed" for v in selected_state),
            "unresolved": sum(v["status"] == "unresolved" for v in selected_state),
            "usage": dict(usage),
            "estimated_cost_usd": cost(usage),
            "max_call_elapsed_s": max(elapsed) if elapsed else None,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        LOCK.unlink(missing_ok=True)


def command_replay(args: argparse.Namespace) -> None:
    """Revalidate sealed provider outputs after deterministic normalizer changes."""
    all_rows = rows()
    by_index = {int(r["item_index"]): r for r in all_rows}
    selected = chosen_indices(all_rows, args.phase)
    state = json.loads(STATE.read_text(encoding="utf-8"))
    codebook = load_json(DEFAULT_CODEBOOK)
    counts: Counter[str] = Counter()
    for index in sorted(selected):
        source = SEALED_ITEMS / f"item_{index:06d}_{args.source_attempt}.json"
        destination = SEALED_ITEMS / f"item_{index:06d}_{args.target_attempt}.json"
        if not source.exists():
            raise FileNotFoundError(source)
        if destination.exists():
            raise FileExistsError(destination)
        sealed = json.loads(source.read_text(encoding="utf-8"))
        try:
            output, actions = normalize_output(sealed["raw_output"])
            derived = validate_output(
                output, by_index[index]["deidentified_title"], codebook
            )
            valid = True
            error = None
            counts["valid"] += 1
        except Exception as exc:  # noqa: BLE001
            output = None
            actions = []
            derived = None
            valid = False
            error = f"{type(exc).__name__}:{exc}"
            counts[error] += 1
        replayed = {
            **sealed,
            "replayed_at": utc_now(),
            "replay_source": str(source.relative_to(ROOT)),
            "normalizer_version": NORMALIZER_VERSION,
            "normalization_policy": str(NORMALIZATION_POLICY.relative_to(ROOT)),
            "valid": valid,
            "output": output,
            "derived": derived,
            "normalization_actions": actions,
            "error": error,
        }
        atomic_json(destination, replayed)
        entry = state[str(index)]
        entry["last_error"] = error
        entry["sealed_item_path"] = str(destination.relative_to(ROOT))
        entry["status"] = "completed" if valid else "unresolved"
    atomic_json(STATE, state)
    print(
        json.dumps(
            {
                "phase": args.phase,
                "replayed": len(selected),
                "normalizer_version": NORMALIZER_VERSION,
                "outcomes": dict(counts),
                "provider_calls": 0,
                "additional_cost_usd": 0.0,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def command_finalize(args: argparse.Namespace) -> None:
    all_rows = rows()
    selected = chosen_indices(all_rows, args.phase)
    state = json.loads(STATE.read_text(encoding="utf-8"))
    output = []
    for index in sorted(selected):
        entry = state[str(index)]
        record = {
            "item_index": index,
            "pilot_phase": entry["pilot_phase"],
            "sampling_stratum": entry["sampling_stratum"],
            "valid": entry["status"] == "completed",
            "error": entry["last_error"],
        }
        if entry.get("sealed_item_path"):
            sealed = json.loads(
                (ROOT / entry["sealed_item_path"]).read_text(encoding="utf-8")
            )
            record.update(
                output=sealed.get("output"),
                derived=sealed.get("derived"),
                normalization_actions=sealed.get("normalization_actions", []),
                call_id=sealed.get("call_id"),
            )
        output.append(record)
    suffix = f"_{args.attempt_label}" if args.attempt_label else ""
    path = RUN_DIR / f"private/{args.phase}{suffix}_records.jsonl"
    atomic_text(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in output))
    calls = []
    for p in SEALED_CALLS.glob("call_*.json"):
        c = json.loads(p.read_text(encoding="utf-8"))
        if c.get("phase") == args.phase:
            calls.append(c)
    usage = Counter()
    for c in calls:
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            usage[key] += int(c.get("usage", {}).get(key, 0) or 0)
    manifest = {
        "created_at": utc_now(),
        "phase": args.phase,
        "status": "complete"
        if all(r["valid"] for r in output)
        else "complete_with_unresolved",
        "records": len(output),
        "structurally_valid": sum(r["valid"] for r in output),
        "unresolved": sum(not r["valid"] for r in output),
        "sealed_calls": len(calls),
        "usage": dict(usage),
        "estimated_cost_usd": cost(usage),
        "normalization": {
            "version": NORMALIZER_VERSION,
            "policy": str(NORMALIZATION_POLICY.relative_to(ROOT)),
            "policy_sha256": sha256(NORMALIZATION_POLICY),
        },
        "output": {str(path.relative_to(ROOT)): sha256(path)},
    }
    atomic_json(RUN_DIR / f"{args.phase}{suffix}_manifest.json", manifest, 0o644)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare")
    sub.add_parser("status")
    run = sub.add_parser("run")
    run.add_argument("--phase", choices=["smoke", "development"], required=True)
    run.add_argument("--workers", type=int, required=True)
    run.add_argument("--batch-size", type=int, required=True)
    run.add_argument("--max-attempts", type=int, default=1)
    run.add_argument("--env-file", type=Path, default=ROOT / ".env.ai-coding")
    replay = sub.add_parser("replay")
    replay.add_argument("--phase", choices=["smoke", "development"], required=True)
    replay.add_argument("--source-attempt", default="attempt_01")
    replay.add_argument("--target-attempt", default="attempt_02")
    finalize = sub.add_parser("finalize")
    finalize.add_argument("--phase", choices=["smoke", "development"], required=True)
    finalize.add_argument("--attempt-label", default="")
    args = parser.parse_args()
    {
        "prepare": command_prepare,
        "status": command_status,
        "run": command_run,
        "replay": command_replay,
        "finalize": command_finalize,
    }[args.command](args)


if __name__ == "__main__":
    main()
