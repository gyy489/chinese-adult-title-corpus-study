"""Resumable v0.2 runner for the frozen 480-row locked pilot sample."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from src.analysis import gendered_argument_visibility_contract_v0_1 as contract_v01
from src.analysis import run_gendered_argument_visibility_pilot_v1 as base
from src.analysis.gendered_argument_visibility_contract_v0_2 import (
    DEFAULT_CODEBOOK,
    DEFAULT_PROMPT,
    DEFAULT_SCHEMA,
    validate_contract_files,
    validate_output,
)

ROOT = Path(__file__).resolve().parents[2]
SOURCE_RUN = ROOT / "data/interim/48_gendered_argument_visibility_pilot_v1"
RUN_DIR = ROOT / "data/interim/49_gendered_argument_visibility_locked_v0_2"
PILOT = SOURCE_RUN / "private/pilot_manifest.csv"
SAMPLE_MANIFEST = SOURCE_RUN / "sample_manifest.json"
CONFIG = ROOT / "config/gendered_argument_visibility_pilot_v1.json"
STATE = RUN_DIR / "state/items.json"
LOCK = RUN_DIR / "state/runner.lock"
SEALED_CALLS = RUN_DIR / "sealed/calls"
SEALED_ITEMS = RUN_DIR / "sealed/items"
PROVIDER_MANIFEST = RUN_DIR / "provider_run_manifest.json"
NORMALIZATION_POLICY = ROOT / "config/gendered_argument_visibility_normalization_v0_2_2.json"
NORMALIZER_VERSION = "argument_visibility_normalizer_v2_2"


_normalize_v01 = base.normalize_output


def normalize_output(raw: Any) -> tuple[dict[str, Any], list[str]]:
    """Apply v0.1 representation repair plus explicit valid-record sentinels."""
    output, actions = _normalize_v01(raw)
    target_fields = {
        "sexual_action_target_realization",
        "control_target_realization",
    }
    actor_labels = {
        "explicit_gendered_person_or_role",
        "explicit_ungendered_person_or_role",
    }
    actor_fields = {
        "sexual_action_actor_realization": "sexual_action_actor_position",
        "control_actor_realization": "control_actor_position",
    }
    invalid_actor_explicit = {
        "explicit_person_or_role",
        "pronoun_or_deictic",
    }
    gendered_positions = {
        "feminine",
        "masculine",
        "gender_diverse",
        "mixed_multiple",
        "mutual_or_reciprocal",
    }
    for field, position_field in actor_fields.items():
        if output.get(field) in invalid_actor_explicit:
            old = output[field]
            output[field] = (
                "explicit_gendered_person_or_role"
                if output.get(position_field) in gendered_positions
                else "explicit_ungendered_person_or_role"
            )
            actions.append(f"field_enum_alias:{field}.{old}={output[field]}")
    for field in target_fields:
        if output.get(field) in actor_labels | {"deidentified_placeholder"}:
            old = output[field]
            output[field] = "explicit_person_or_role"
            actions.append(f"field_enum_alias:{field}.{old}=explicit_person_or_role")
    for item in output.get("evidence", []):
        if not isinstance(item, dict):
            continue
        for support in item.get("supports", []):
            if (
                isinstance(support, dict)
                and support.get("field") in target_fields
                and support.get("code") in actor_labels
            ):
                old = support["code"]
                support["code"] = "explicit_person_or_role"
                actions.append(
                    f"field_enum_alias:evidence.{support['field']}.{old}=explicit_person_or_role"
                )
    for field, position_field in actor_fields.items():
        position = output.get(position_field)
        realization = output.get(field)
        if position != "not_visible" and realization in {
            "event_visible_actor_not_visible",
            "passive_agent_omitted",
        }:
            output[field] = (
                "explicit_gendered_person_or_role"
                if position in gendered_positions
                else "explicit_ungendered_person_or_role"
            )
            actions.append(f"visible_actor_realization:{field}={output[field]}")
    for prefix in ("sexual_action", "control"):
        position_field = f"{prefix}_target_position"
        realization_field = f"{prefix}_target_realization"
        if (
            output.get(f"{prefix}_visibility") in {"explicit", "implied"}
            and output.get(position_field) == "not_visible"
            and output.get(realization_field) != "object_omitted"
        ):
            output[realization_field] = "object_omitted"
            actions.append(f"invisible_target:{realization_field}=object_omitted")
    for extra in (
        "material_creation_actor_realization",
        "material_creation_target_realization",
    ):
        if extra in output:
            output.pop(extra)
            actions.append(f"remove_extra:{extra}")
    if isinstance(output.get("context_frame_codes"), list):
        replaced = [
            "incapacity_or_deception_explicit" if code == "deception_or_incapacity" else code
            for code in output["context_frame_codes"]
        ]
        if replaced != output["context_frame_codes"]:
            output["context_frame_codes"] = replaced
            actions.append("enum_alias:context_frame_codes.deception_or_incapacity")
    if output.get("person_reference_status") == "visible" and not output.get("visible_gender_positions"):
        position_values = {
            output.get(field)
            for field in contract_v01.POSITION_FIELDS | {"person_focus_position"}
        }
        inferred = sorted(position_values & {"feminine", "masculine", "gender_diverse", "gender_unspecified"})
        output["visible_gender_positions"] = inferred or ["gender_unspecified"]
        if output.get("participant_configuration") == "none":
            output["participant_configuration"] = "single"
        actions.append("internal_consistency:visible_gender_positions")
    if output.get("record_validity") != "valid":
        return output, actions
    scalar = {
        "desire_holder_position": "not_visible",
        "pleasure_holder_position": "not_visible",
        "refusal_resistance_position": "not_visible",
        "attributed_speech_position": "not_visible",
        "objectification_target_position": "not_visible",
        "consent_visibility": "no_visible_consent_language",
    }
    arrays = {
        "attributed_speech_act": ["no_visible_attributed_speech"],
        "editorial_framing_cues": ["no_visible_editorial_frame"],
        "context_frame_codes": ["none_visible"],
    }
    for field, replacement in scalar.items():
        if output.get(field) == "not_applicable":
            output[field] = replacement
            actions.append(f"valid_sentinel:{field}={replacement}")
    for field, replacement in arrays.items():
        if output.get(field) == ["not_applicable"]:
            output[field] = replacement
            actions.append(f"valid_sentinel:{field}={replacement[0]}")
    return output, list(dict.fromkeys(actions))


def chosen_indices(all_rows: list[dict[str, str]], phase: str) -> set[int]:
    locked = sorted(
        (r for r in all_rows if r["pilot_phase"] == "locked"),
        key=lambda r: int(r["item_index"]),
    )
    if phase == "smoke":
        return {int(r["item_index"]) for r in locked[:12]}
    if phase == "locked":
        return {int(r["item_index"]) for r in locked}
    raise ValueError(f"unknown phase: {phase}")


def configure_base() -> None:
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
    base.normalize_output = normalize_output
    base.validate_output = validate_output
    base.validate_contract_files = validate_contract_files
    base.chosen_indices = chosen_indices


def command_prepare(_args: argparse.Namespace) -> None:
    contract = validate_contract_files()
    all_rows = base.rows()
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
    base.atomic_json(STATE, state)
    manifest = {
        "created_at": base.utc_now(),
        "status": "prepared_not_run",
        "model": base.MODEL,
        "endpoint": base.ENDPOINT,
        "locked_records": len(chosen_indices(all_rows, "locked")),
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
            "smoke": {"records": 12, "source": "first 12 locked rows", "included_in_locked_total": True},
            "locked": {"records": 480, "batch_size": 10, "workers": 12, "max_attempts": 1},
        },
        "contract_version": contract["codebook"]["version"],
        "normalizer_version": NORMALIZER_VERSION,
        "payload_policy": "Only batch_id, item_index and deidentified title are sent.",
        "credential_status": "checked at run time; credential value is never logged",
    }
    base.atomic_json(PROVIDER_MANIFEST, manifest, 0o644)
    print(json.dumps({"prepared": len(state), "locked": 480}, ensure_ascii=False))


def main() -> None:
    configure_base()
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare")
    sub.add_parser("status")
    run = sub.add_parser("run")
    run.add_argument("--phase", choices=["smoke", "locked"], required=True)
    run.add_argument("--workers", type=int, required=True)
    run.add_argument("--batch-size", type=int, required=True)
    run.add_argument("--max-attempts", type=int, default=1)
    run.add_argument("--env-file", type=Path, default=ROOT / ".env.ai-coding")
    finalize = sub.add_parser("finalize")
    finalize.add_argument("--phase", choices=["smoke", "locked"], required=True)
    finalize.add_argument("--attempt-label", default="")
    replay = sub.add_parser("replay")
    replay.add_argument("--phase", choices=["smoke", "locked"], required=True)
    replay.add_argument("--source-attempt", default="attempt_01")
    replay.add_argument("--target-attempt", default="attempt_02")
    args = parser.parse_args()
    {
        "prepare": command_prepare,
        "status": base.command_status,
        "run": base.command_run,
        "finalize": base.command_finalize,
        "replay": base.command_replay,
    }[args.command](args)


if __name__ == "__main__":
    main()
