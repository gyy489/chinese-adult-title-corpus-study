"""Contract for the 54th long-field reduced deep-annotation validation.

The 15 label fields use the v0.3 long names and long enum values. Evidence
coverage is deliberately derived QA: missing evidence does not invalidate an
otherwise analyzable label object. Malformed evidence, non-exact spans, field/
code mismatches, schema drift, and conditional-dependency failures remain hard
contract errors.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CODEBOOK = ROOT / "config/gendered_argument_visibility_longfield_deep_codebook_v1.json"
DEFAULT_SCHEMA = ROOT / "config/gendered_argument_visibility_longfield_deep_output_schema_v1.json"
DEFAULT_PROMPT = ROOT / "prompts/gendered_argument_visibility_longfield_deep_validation_v1.md"
PROMPT_PLACEHOLDER = "{{CODEBOOK_JSON}}"

LABEL_FIELDS = (
    "record_validity",
    "material_creation_visibility",
    "material_creation_target_position",
    "distribution_visibility",
    "distribution_actor_position",
    "distribution_actor_realization",
    "distribution_target_position",
    "distribution_authorization_visibility",
    "exposure_visibility",
    "exposure_target_position",
    "objectification_target_position",
    "desire_holder_position",
    "pleasure_holder_position",
    "refusal_resistance_position",
    "attributed_speech_position",
)
QUALITY_FIELDS = ("adjudication_reasons", "evidence")
ALL_FIELDS = LABEL_FIELDS + QUALITY_FIELDS
VISIBLE_ACTOR_REALIZATIONS = {
    "explicit_gendered_person_or_role",
    "explicit_ungendered_person_or_role",
    "generic_or_collective_actor",
    "zero_anaphora_coreferential",
}
OMITTED_ACTOR_REALIZATIONS = {
    "passive_agent_omitted",
    "nominalized_event_without_agent",
    "no_actor_expression",
}
NO_EVIDENCE_VALUES = {
    "absent",
    "not_visible",
    "not_applicable",
    "no_visible_authorization_language",
    "no_actor_expression",
}


class ContractError(ValueError):
    """Raised when a response violates the frozen validation contract."""


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ContractError(f"{path} must contain a JSON object")
    return value


def render_system_prompt(codebook: dict[str, Any], template: str | None = None) -> str:
    value = template if template is not None else DEFAULT_PROMPT.read_text(encoding="utf-8")
    if value.count(PROMPT_PLACEHOLDER) != 1:
        raise ContractError("prompt must contain exactly one codebook placeholder")
    compact = json.dumps(codebook, ensure_ascii=False, separators=(",", ":"))
    return value.replace(PROMPT_PLACEHOLDER, compact)


def enum_values(codebook: dict[str, Any], field: str) -> set[str]:
    try:
        spec = codebook["fields"][field]
    except KeyError as exc:
        raise ContractError(f"unknown field: {field}") from exc
    values = spec.get("enum")
    if values is None and "enum_ref" in spec:
        values = codebook["enums"].get(spec["enum_ref"])
    if not isinstance(values, list) or not values or any(not isinstance(v, str) for v in values):
        raise ContractError(f"field {field} has no valid enum")
    if len(values) != len(set(values)):
        raise ContractError(f"field {field} enum contains duplicates")
    return set(values)


def _validate_enum(item: dict[str, Any], codebook: dict[str, Any], field: str) -> None:
    if item[field] not in enum_values(codebook, field):
        raise ContractError(f"{field} has invalid value: {item[field]!r}")


def _validate_stage(item: dict[str, Any], visibility: str, target: str) -> None:
    stage = item[visibility]
    position = item[target]
    if stage == "absent" and position != "not_visible":
        raise ContractError(f"{visibility}=absent requires {target}=not_visible")
    if stage == "not_applicable" and position != "not_applicable":
        raise ContractError(f"{visibility}=not_applicable requires {target}=not_applicable")
    if stage == "unclear" and position != "unclear":
        raise ContractError(f"{visibility}=unclear requires {target}=unclear")
    if stage == "visible" and position == "not_applicable":
        raise ContractError(f"{visibility}=visible is incompatible with {target}=not_applicable")


def expected_evidence_pairs(item: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        (field, str(item[field]))
        for field in LABEL_FIELDS
        if field != "record_validity" and item[field] not in NO_EVIDENCE_VALUES
    }


def validate_long_item(
    item: dict[str, Any], title: str, codebook: dict[str, Any]
) -> dict[str, Any]:
    """Validate hard structure and return evidence completeness as derived QA."""
    if not isinstance(item, dict):
        raise ContractError("validation item must be an object")
    expected_keys = {"i", *ALL_FIELDS}
    if set(item) != expected_keys:
        raise ContractError(
            f"item fields differ; missing={sorted(expected_keys - set(item))}, "
            f"extra={sorted(set(item) - expected_keys)}"
        )
    if not isinstance(item["i"], int) or isinstance(item["i"], bool) or item["i"] < 1:
        raise ContractError("i must be a positive integer")
    for field in LABEL_FIELDS:
        _validate_enum(item, codebook, field)

    reasons = item["adjudication_reasons"]
    if not isinstance(reasons, list) or len(reasons) != len(set(reasons)) or any(
        not isinstance(reason, str) or not reason.strip() or len(reason) > 240
        for reason in reasons
    ):
        raise ContractError("adjudication_reasons must contain unique 1-240 character strings")
    if any(item[field] == "unclear" for field in LABEL_FIELDS) and not reasons:
        raise ContractError("every unclear label requires an adjudication reason")

    if item["record_validity"] != "valid":
        if any(item[field] != "not_applicable" for field in LABEL_FIELDS[1:]):
            raise ContractError("non-valid records require every annotation label=not_applicable")
        if item["evidence"]:
            raise ContractError("non-valid records require evidence=[]")
    else:
        _validate_stage(
            item, "material_creation_visibility", "material_creation_target_position"
        )
        _validate_stage(item, "exposure_visibility", "exposure_target_position")
        distribution = item["distribution_visibility"]
        arguments = (
            item["distribution_actor_position"],
            item["distribution_actor_realization"],
            item["distribution_target_position"],
            item["distribution_authorization_visibility"],
        )
        if distribution == "absent" and arguments != (
            "not_visible",
            "not_applicable",
            "not_visible",
            "not_applicable",
        ):
            raise ContractError(
                "distribution_visibility=absent requires actor=not_visible, "
                "realization=not_applicable, target=not_visible, authorization=not_applicable"
            )
        if distribution == "not_applicable" and any(
            value != "not_applicable" for value in arguments
        ):
            raise ContractError(
                "distribution_visibility=not_applicable requires all distribution arguments=not_applicable"
            )
        if distribution == "unclear" and "unclear" not in arguments:
            raise ContractError(
                "distribution_visibility=unclear requires an unclear distribution argument"
            )
        if distribution == "visible":
            actor = item["distribution_actor_position"]
            realization = item["distribution_actor_realization"]
            if actor == "not_applicable" or item["distribution_target_position"] == "not_applicable":
                raise ContractError("visible distribution is incompatible with inapplicable positions")
            if item["distribution_authorization_visibility"] == "not_applicable":
                raise ContractError("visible distribution requires applicable authorization visibility")
            if actor == "not_visible" and realization not in OMITTED_ACTOR_REALIZATIONS:
                raise ContractError("visible distribution with omitted actor requires an omission realization")
            if actor not in {"not_visible", "unclear"} and realization not in VISIBLE_ACTOR_REALIZATIONS:
                raise ContractError("visible distribution actor requires a visible actor realization")
            if actor == "unclear" and realization != "unclear":
                raise ContractError("unclear distribution actor requires unclear realization")

    evidence = item["evidence"]
    if not isinstance(evidence, list):
        raise ContractError("evidence must be an array")
    expected_pairs = expected_evidence_pairs(item)
    provided_pairs: set[tuple[str, str]] = set()
    provided_links: set[tuple[str, str, str]] = set()
    for row_index, row in enumerate(evidence):
        if not isinstance(row, dict) or set(row) != {"span", "supports"}:
            raise ContractError(f"evidence[{row_index}] must contain exactly span/supports")
        span = row["span"]
        if not isinstance(span, str) or not span or len(span) > 160 or span not in title:
            raise ContractError(f"evidence[{row_index}].span must be an exact title substring")
        supports = row["supports"]
        if not isinstance(supports, list) or not supports:
            raise ContractError(f"evidence[{row_index}].supports must be a non-empty array")
        for support in supports:
            if not isinstance(support, dict) or set(support) != {"field", "code"}:
                raise ContractError("each evidence support must contain exactly field/code")
            field = support["field"]
            code = support["code"]
            if field not in LABEL_FIELDS or field == "record_validity":
                raise ContractError(f"unsupported evidence field: {field!r}")
            if code not in enum_values(codebook, field):
                raise ContractError(f"evidence has invalid code for {field}: {code!r}")
            pair = (field, code)
            if item[field] != code or pair not in expected_pairs:
                raise ContractError(f"evidence support does not match selected substantive code: {pair}")
            provided_pairs.add(pair)
            provided_links.add((field, code, span))

    missing = expected_pairs - provided_pairs
    pair_precision = 1.0
    pair_recall = (
        len(provided_pairs) / len(expected_pairs) if expected_pairs else 1.0
    )
    return {
        "contract_valid": True,
        "needs_adjudication": bool(reasons),
        "evidence_rows_valid": True,
        "evidence_complete": not missing,
        "expected_evidence_pairs": sorted(expected_pairs),
        "provided_evidence_pairs": sorted(provided_pairs),
        "provided_evidence_links": sorted(provided_links),
        "missing_evidence_pairs": sorted(missing),
        "evidence_pair_precision": pair_precision,
        "evidence_pair_recall": pair_recall,
    }


def validate_response(
    value: Any, titles: dict[int, str], codebook: dict[str, Any]
) -> list[dict[str, Any]]:
    if not isinstance(value, dict) or set(value) != {"items"}:
        raise ContractError("response must contain exactly top-level items")
    items = value["items"]
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ContractError("items must be an array of objects")
    indices = [item.get("i") for item in items]
    if len(indices) != len(set(indices)) or set(indices) != set(titles):
        raise ContractError("response index set differs from request")
    for item in items:
        validate_long_item(item, titles[item["i"]], codebook)
    return sorted(items, key=lambda item: item["i"])


def validate_contract_files() -> dict[str, Any]:
    codebook = load_json(DEFAULT_CODEBOOK)
    schema = load_json(DEFAULT_SCHEMA)
    rendered = render_system_prompt(codebook)
    if codebook.get("version") != "1.0.0":
        raise ContractError("unexpected codebook version")
    if tuple(codebook.get("fields", {})) != ALL_FIELDS:
        raise ContractError("codebook field order/set differs from contract")
    item_schema = schema.get("$defs", {}).get("item", {})
    required = set(item_schema.get("required", []))
    properties = set(item_schema.get("properties", {}))
    expected = {"i", *ALL_FIELDS}
    if required != expected or properties != expected:
        raise ContractError("schema item fields differ from contract")
    if "privacy_chain_relevance" in rendered or '"needs_adjudication"' in rendered:
        raise ContractError("excluded model-return fields leaked into rendered prompt")
    if PROMPT_PLACEHOLDER in rendered:
        raise ContractError("rendered prompt contains unresolved placeholder")
    for field in LABEL_FIELDS:
        enum_values(codebook, field)
    return {"codebook": codebook, "schema": schema, "rendered_prompt": rendered}
