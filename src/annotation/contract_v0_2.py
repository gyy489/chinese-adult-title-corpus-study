"""v0.2 structural contract for the locked argument-visibility pilot."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from src.annotation import contract_v0_1 as v01

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CODEBOOK = ROOT / "config/gendered_argument_visibility_codebook_v0_2.json"
DEFAULT_SCHEMA = ROOT / "config/gendered_argument_visibility_output_schema_v0_1.json"
DEFAULT_PROMPT = ROOT / "prompts/gendered_argument_visibility_annotation_v0_2.md"
PROMPT_PLACEHOLDER = v01.PROMPT_PLACEHOLDER
POSITION_EVIDENCE_FIELDS = v01.POSITION_EVIDENCE_FIELDS
ContractError = v01.ContractError
load_json = v01.load_json
render_system_prompt = v01.render_system_prompt

NONCONDITIONAL_POSITION_FIELDS = {
    "desire_holder_position",
    "pleasure_holder_position",
    "refusal_resistance_position",
    "attributed_speech_position",
    "objectification_target_position",
}


def validate_output(
    output: dict[str, Any], title: str, codebook: dict[str, Any]
) -> dict[str, bool]:
    # Validate semantic structure independently from evidence-link completeness.
    # The synthetic evidence exists only in memory and is never persisted.
    structural = copy.deepcopy(output)
    person_structure_consistent = not (
        output.get("person_reference_status") == "visible"
        and (
            output.get("participant_configuration") in {"none", "not_applicable"}
            or not output.get("visible_gender_positions")
        )
    )
    if not person_structure_consistent:
        if structural.get("participant_configuration") in {"none", "not_applicable"}:
            structural["participant_configuration"] = "single"
        if not structural.get("visible_gender_positions"):
            structural["visible_gender_positions"] = ["gender_unspecified"]
    selected = v01._selected_pairs(structural, codebook)
    structural["evidence"] = (
        [{"span": title[:160], "supports": [
            {"field": field, "code": code} for field, code in sorted(selected)
        ]}]
        if selected
        else []
    )
    derived = v01.validate_output(structural, title, codebook)
    derived["person_structure_consistent"] = person_structure_consistent
    seen: set[tuple[str, str]] = set()
    evidence_rows_valid = isinstance(output.get("evidence"), list)
    if evidence_rows_valid:
        for item in output["evidence"]:
            if not isinstance(item, dict) or set(item) != {"span", "supports"}:
                evidence_rows_valid = False
                continue
            span = item.get("span")
            supports = item.get("supports")
            if (
                not isinstance(span, str)
                or not span
                or len(span) > 160
                or span not in title
                or not isinstance(supports, list)
                or not supports
            ):
                evidence_rows_valid = False
                continue
            for support in supports:
                if not isinstance(support, dict) or set(support) != {"field", "code"}:
                    evidence_rows_valid = False
                    continue
                pair = (support["field"], support["code"])
                if pair not in selected:
                    evidence_rows_valid = False
                else:
                    seen.add(pair)
    derived["evidence_rows_valid"] = evidence_rows_valid
    derived["evidence_complete"] = evidence_rows_valid and seen == selected
    if output["record_validity"] != "valid":
        return derived
    for field in NONCONDITIONAL_POSITION_FIELDS:
        if output[field] == "not_applicable":
            raise ContractError(f"valid record forbids not_applicable in {field}")
    required = {
        "consent_visibility": "no_visible_consent_language",
        "attributed_speech_act": ["no_visible_attributed_speech"],
        "editorial_framing_cues": ["no_visible_editorial_frame"],
        "context_frame_codes": ["none_visible"],
    }
    for field, absent_value in required.items():
        value = output[field]
        if value == "not_applicable" or value == ["not_applicable"]:
            raise ContractError(
                f"valid record uses {absent_value!r}, not not_applicable, in {field}"
            )
    return derived


def validate_contract_files() -> dict[str, Any]:
    codebook = load_json(DEFAULT_CODEBOOK)
    schema = load_json(DEFAULT_SCHEMA)
    template = DEFAULT_PROMPT.read_text(encoding="utf-8")
    rendered = render_system_prompt(codebook, template)
    required = v01.required_keys(codebook)
    if set(schema.get("required", [])) != required or set(schema.get("properties", {})) != required:
        raise ContractError("schema/codebook field sets differ")
    if set(schema["$defs"]["position"]["enum"]) != set(codebook["common_positions"]):
        raise ContractError("position enums differ")
    if set(schema["$defs"]["actorRealization"]["enum"]) != set(codebook["actor_realizations"]):
        raise ContractError("actor realization enums differ")
    if set(schema["$defs"]["targetRealization"]["enum"]) != set(codebook["target_realizations"]):
        raise ContractError("target realization enums differ")
    if PROMPT_PLACEHOLDER in rendered:
        raise ContractError("rendered prompt contains unresolved placeholder")
    return {"codebook": codebook, "schema": schema, "rendered_prompt": rendered}
