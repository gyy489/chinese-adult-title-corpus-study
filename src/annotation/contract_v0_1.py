"""Render and validate the gendered-argument-visibility pilot contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CODEBOOK = ROOT / "config/gendered_argument_visibility_codebook_v0_1.json"
DEFAULT_SCHEMA = ROOT / "config/gendered_argument_visibility_output_schema_v0_1.json"
DEFAULT_PROMPT = ROOT / "prompts/gendered_argument_visibility_annotation_v0_1.md"
PROMPT_PLACEHOLDER = "{{CODEBOOK_JSON}}"

QUALITY_FIELDS = {"needs_adjudication", "adjudication_reasons", "evidence"}
POSITION_FIELDS = {
    "sexual_action_actor_position",
    "sexual_action_target_position",
    "control_actor_position",
    "control_target_position",
    "desire_holder_position",
    "pleasure_holder_position",
    "refusal_resistance_position",
    "attributed_speech_position",
    "objectification_target_position",
    "material_creation_actor_position",
    "material_creation_target_position",
    "acquisition_actor_position",
    "acquisition_target_position",
    "distribution_actor_position",
    "distribution_target_position",
    "exposure_target_position",
}
ACTOR_REALIZATION_FIELDS = {
    "sexual_action_actor_realization",
    "control_actor_realization",
}
TARGET_REALIZATION_FIELDS = {
    "sexual_action_target_realization",
    "control_target_realization",
}
MULTI_FIELDS = {
    "visible_gender_positions",
    "control_form",
    "attributed_speech_act",
    "editorial_framing_cues",
    "context_frame_codes",
}
STAGES = {
    "material_creation": (
        "material_creation_actor_position",
        "material_creation_target_position",
    ),
    "acquisition": ("acquisition_actor_position", "acquisition_target_position"),
    "distribution": ("distribution_actor_position", "distribution_target_position"),
    "exposure": (None, "exposure_target_position"),
}

POSITION_EVIDENCE_FIELDS = POSITION_FIELDS | {"person_focus_position"}


class ContractError(ValueError):
    """Raised when a provider item violates the frozen pilot contract."""


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ContractError(f"{path} must contain an object")
    return value


def field_map(codebook: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {field["id"]: field for field in codebook["fields"]}


def required_keys(codebook: dict[str, Any]) -> set[str]:
    return set(field_map(codebook))


def options(codebook: dict[str, Any], field: str) -> set[str]:
    spec = field_map(codebook)[field]
    kind = spec["type"]
    if kind == "position":
        return set(codebook["common_positions"])
    if kind == "actor_realization":
        return set(codebook["actor_realizations"])
    if kind == "target_realization":
        return set(codebook["target_realizations"])
    return set(spec.get("options", []))


def render_system_prompt(codebook: dict[str, Any], template: str) -> str:
    if template.count(PROMPT_PLACEHOLDER) != 1:
        raise ContractError("prompt must contain exactly one codebook placeholder")
    encoded = json.dumps(
        codebook, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return template.replace(PROMPT_PLACEHOLDER, encoded)


def _enum(output: dict[str, Any], field: str, allowed: set[str]) -> str:
    value = output[field]
    if not isinstance(value, str) or value not in allowed:
        raise ContractError(f"{field} must be one of {sorted(allowed)}")
    return value


def _array(output: dict[str, Any], field: str, allowed: set[str]) -> list[str]:
    value = output[field]
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ContractError(f"{field} must be a string array")
    if len(value) != len(set(value)):
        raise ContractError(f"{field} contains duplicates")
    if invalid := set(value) - allowed:
        raise ContractError(f"{field} contains invalid values: {sorted(invalid)}")
    return value


def _validate_exclusive_arrays(
    output: dict[str, Any], codebook: dict[str, Any]
) -> None:
    for field, exclusive in codebook["exclusive_multi_values"].items():
        values = output[field]
        if len(values) > 1 and set(values) & set(exclusive):
            raise ContractError(
                f"{field} exclusive sentinel cannot coexist with other values"
            )


def _validate_nonvalid(output: dict[str, Any], codebook: dict[str, Any]) -> None:
    for field, spec in field_map(codebook).items():
        if field in QUALITY_FIELDS or field == "record_validity":
            continue
        if field == "visible_gender_positions":
            if output[field]:
                raise ContractError(
                    "non-valid record requires empty visible_gender_positions"
                )
        elif spec["type"] == "multi":
            if output[field] != ["not_applicable"]:
                raise ContractError(
                    f"non-valid record requires {field}=[not_applicable]"
                )
        elif output[field] != "not_applicable":
            raise ContractError(f"non-valid record requires {field}=not_applicable")
    if output["evidence"]:
        raise ContractError("non-valid record requires empty evidence")


def _validate_person(output: dict[str, Any]) -> None:
    status = output["person_reference_status"]
    if status == "absent":
        if (
            output["participant_configuration"] != "none"
            or output["visible_gender_positions"]
            or output["person_focus_position"] != "no_person_focus"
        ):
            raise ContractError("absent person requires none/empty/no_person_focus")
    elif status == "visible":
        if (
            output["participant_configuration"] in {"none", "not_applicable"}
            or not output["visible_gender_positions"]
        ):
            raise ContractError(
                "visible person requires a non-none configuration and visible gender position"
            )
    elif status == "unclear" and (
        output["participant_configuration"] != "unclear"
        or output["person_focus_position"] != "unclear"
    ):
        raise ContractError("unclear person requires unclear configuration and focus")


def _validate_event(
    output: dict[str, Any],
    prefix: str,
    actor_field: str,
    target_field: str,
    actor_realization: str,
    target_realization: str,
) -> None:
    visibility = output[f"{prefix}_visibility"]
    actor = output[actor_field]
    target = output[target_field]
    actor_r = output[actor_realization]
    target_r = output[target_realization]
    if visibility == "absent":
        if (actor, target, actor_r, target_r) != (
            "not_visible",
            "not_visible",
            "not_applicable",
            "not_applicable",
        ):
            raise ContractError(
                f"absent {prefix} requires invisible positions and inapplicable realizations"
            )
    elif visibility == "unclear":
        if "unclear" not in {actor, target, actor_r, target_r}:
            raise ContractError(
                f"unclear {prefix} requires at least one unclear argument field"
            )
    elif visibility in {"explicit", "implied"}:
        if actor == "not_visible" and actor_r not in {
            "passive_agent_omitted",
            "event_visible_actor_not_visible",
        }:
            raise ContractError(
                f"invisible {prefix} actor requires an omission realization"
            )
        if actor != "not_visible" and actor_r in {
            "passive_agent_omitted",
            "event_visible_actor_not_visible",
            "not_applicable",
        }:
            raise ContractError(
                f"visible {prefix} actor is incompatible with omission realization"
            )
        if target == "not_visible" and target_r != "object_omitted":
            raise ContractError(f"invisible {prefix} target requires object_omitted")
        if target != "not_visible" and target_r in {"object_omitted", "not_applicable"}:
            raise ContractError(
                f"visible {prefix} target is incompatible with object_omitted"
            )


def _validate_control(output: dict[str, Any]) -> None:
    visibility = output["control_visibility"]
    forms = output["control_form"]
    if visibility == "absent" and forms != ["none_visible"]:
        raise ContractError("absent control requires control_form=[none_visible]")
    if visibility == "unclear" and forms != ["unclear"]:
        raise ContractError("unclear control requires control_form=[unclear]")
    if visibility in {"explicit", "implied"} and set(forms) & {
        "none_visible",
        "unclear",
        "not_applicable",
    }:
        raise ContractError("visible control requires substantive control_form")
    _validate_event(
        output,
        "control",
        "control_actor_position",
        "control_target_position",
        "control_actor_realization",
        "control_target_realization",
    )


def _validate_privacy(output: dict[str, Any]) -> None:
    relevance = output["privacy_chain_relevance"]
    if relevance == "not_relevant":
        for stage, (actor, target) in STAGES.items():
            if output[f"{stage}_visibility"] != "not_applicable":
                raise ContractError(
                    "non-relevant privacy module requires inapplicable stages"
                )
            for field in (actor, target):
                if field and output[field] != "not_applicable":
                    raise ContractError(
                        "non-relevant privacy module requires inapplicable positions"
                    )
        if output["distribution_authorization_visibility"] != "not_applicable":
            raise ContractError(
                "non-relevant privacy module requires inapplicable authorization"
            )
        return
    if relevance == "unclear":
        if not output["needs_adjudication"]:
            raise ContractError("unclear privacy relevance requires adjudication")
        return
    for stage, (actor, target) in STAGES.items():
        visibility = output[f"{stage}_visibility"]
        if visibility == "not_applicable":
            raise ContractError(
                "relevant privacy module cannot use inapplicable stage visibility"
            )
        fields = [field for field in (actor, target) if field]
        if visibility == "absent" and any(
            output[field] != "not_visible" for field in fields
        ):
            raise ContractError(
                f"absent privacy stage {stage} requires not_visible positions"
            )
        if visibility == "unclear" and all(
            output[field] != "unclear" for field in fields
        ):
            raise ContractError(
                f"unclear privacy stage {stage} requires an unclear position"
            )
    distribution = output["distribution_visibility"]
    authorization = output["distribution_authorization_visibility"]
    if distribution == "absent" and authorization != "not_applicable":
        raise ContractError("absent distribution requires inapplicable authorization")
    if distribution == "visible" and authorization == "not_applicable":
        raise ContractError(
            "visible distribution requires authorization visibility coding"
        )


def _selected_pairs(
    output: dict[str, Any], codebook: dict[str, Any]
) -> set[tuple[str, str]]:
    skip = set(codebook["evidence_policy"]["values_not_requiring_evidence"]) | {
        "no_visible_authorization_language"
    }
    selected: set[tuple[str, str]] = set()
    for field in field_map(codebook):
        if field in QUALITY_FIELDS or field == "record_validity":
            continue
        value = output[field]
        values = value if isinstance(value, list) else [value]
        for code in values:
            if code not in skip:
                selected.add((field, code))
    return selected


def _validate_evidence(
    output: dict[str, Any], title: str, codebook: dict[str, Any]
) -> None:
    evidence = output["evidence"]
    if not isinstance(evidence, list):
        raise ContractError("evidence must be an array")
    expected = _selected_pairs(output, codebook)
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(evidence):
        if not isinstance(item, dict) or set(item) != {"span", "supports"}:
            raise ContractError(f"evidence[{index}] must contain exactly span/supports")
        span = item["span"]
        if (
            not isinstance(span, str)
            or not span
            or len(span) > 160
            or span not in title
        ):
            raise ContractError(
                f"evidence[{index}].span must be an exact title substring"
            )
        supports = item["supports"]
        if not isinstance(supports, list) or not supports:
            raise ContractError(f"evidence[{index}].supports must be non-empty")
        for support in supports:
            if not isinstance(support, dict) or set(support) != {"field", "code"}:
                raise ContractError("evidence support must contain exactly field/code")
            pair = (support["field"], support["code"])
            if pair not in expected:
                raise ContractError(
                    f"evidence support does not match selected code: {pair}"
                )
            seen.add(pair)
    if missing := expected - seen:
        raise ContractError(f"selected codes lack evidence: {sorted(missing)}")


def validate_output(
    output: dict[str, Any], title: str, codebook: dict[str, Any]
) -> dict[str, bool]:
    if not isinstance(output, dict):
        raise ContractError("model output must be an object")
    required = required_keys(codebook)
    if set(output) != required:
        raise ContractError(
            f"output fields differ; missing={sorted(required - set(output))}, extra={sorted(set(output) - required)}"
        )
    specs = field_map(codebook)
    for field, spec in specs.items():
        kind = spec["type"]
        if kind in {"boolean", "string_array", "evidence_array"}:
            continue
        if kind == "multi":
            _array(output, field, options(codebook, field))
        else:
            _enum(output, field, options(codebook, field))
    _validate_exclusive_arrays(output, codebook)
    if not isinstance(output["needs_adjudication"], bool):
        raise ContractError("needs_adjudication must be boolean")
    reasons = output["adjudication_reasons"]
    if not isinstance(reasons, list) or any(
        not isinstance(reason, str) or not reason.strip() or len(reason) > 240
        for reason in reasons
    ):
        raise ContractError("adjudication_reasons must contain 1-240 character strings")
    if output["needs_adjudication"] != bool(reasons):
        raise ContractError("needs_adjudication must match adjudication_reasons")
    unclear = any(
        value == "unclear" or (isinstance(value, list) and "unclear" in value)
        for field, value in output.items()
        if field not in QUALITY_FIELDS
    )
    if unclear and not output["needs_adjudication"]:
        raise ContractError("every unclear code requires adjudication")
    if output["record_validity"] != "valid":
        _validate_nonvalid(output, codebook)
    else:
        _validate_person(output)
        _validate_event(
            output,
            "sexual_action",
            "sexual_action_actor_position",
            "sexual_action_target_position",
            "sexual_action_actor_realization",
            "sexual_action_target_realization",
        )
        _validate_control(output)
        _validate_privacy(output)
    _validate_evidence(output, title, codebook)
    substantive = {
        "feminine",
        "masculine",
        "gender_diverse",
        "mixed_multiple",
        "mutual_or_reciprocal",
        "gender_unspecified",
    }
    return {
        "sexual_action_actor_determinate": output["sexual_action_actor_position"]
        in substantive,
        "control_actor_determinate": output["control_actor_position"] in substantive,
        "control_target_determinate": output["control_target_position"] in substantive,
        "attributed_speech_visible": output["attributed_speech_position"]
        in substantive,
        "privacy_distribution_actor_determinate": output["distribution_actor_position"]
        in substantive,
    }


def validate_contract_files() -> dict[str, Any]:
    codebook = load_json(DEFAULT_CODEBOOK)
    schema = load_json(DEFAULT_SCHEMA)
    template = DEFAULT_PROMPT.read_text(encoding="utf-8")
    rendered = render_system_prompt(codebook, template)
    required = required_keys(codebook)
    if (
        set(schema.get("required", [])) != required
        or set(schema.get("properties", {})) != required
    ):
        raise ContractError("schema/codebook field sets differ")
    if set(schema["$defs"]["position"]["enum"]) != set(codebook["common_positions"]):
        raise ContractError("position enums differ")
    if set(schema["$defs"]["actorRealization"]["enum"]) != set(
        codebook["actor_realizations"]
    ):
        raise ContractError("actor realization enums differ")
    if set(schema["$defs"]["targetRealization"]["enum"]) != set(
        codebook["target_realizations"]
    ):
        raise ContractError("target realization enums differ")
    if PROMPT_PLACEHOLDER in rendered:
        raise ContractError("rendered prompt contains unresolved placeholder")
    return {"codebook": codebook, "schema": schema, "rendered_prompt": rendered}
