"""v0.3 structural contract for the 5,000-row scale-up."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.analysis import gendered_argument_visibility_contract_v0_1 as v01
from src.analysis import gendered_argument_visibility_contract_v0_2 as v02

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CODEBOOK = ROOT / "config/gendered_argument_visibility_codebook_v0_3.json"
DEFAULT_SCHEMA = ROOT / "config/gendered_argument_visibility_output_schema_v0_3.json"
DEFAULT_PROMPT = ROOT / "prompts/gendered_argument_visibility_annotation_v0_3.md"
PROMPT_PLACEHOLDER = v01.PROMPT_PLACEHOLDER
ContractError = v01.ContractError
load_json = v01.load_json
render_system_prompt = v01.render_system_prompt

VISIBLE_DISTRIBUTION_ACTORS = {
    "explicit_gendered_person_or_role",
    "explicit_ungendered_person_or_role",
    "generic_or_collective_actor",
    "zero_anaphora_coreferential",
}
OMITTED_DISTRIBUTION_ACTORS = {
    "passive_agent_omitted",
    "nominalized_event_without_agent",
    "no_actor_expression",
}


def validate_output(
    output: dict[str, Any], title: str, codebook: dict[str, Any]
) -> dict[str, bool]:
    derived = v02.validate_output(output, title, codebook)
    if output["record_validity"] != "valid":
        return derived
    relevance = output["privacy_chain_relevance"]
    visibility = output["distribution_visibility"]
    position = output["distribution_actor_position"]
    realization = output["distribution_actor_realization"]
    if relevance == "not_relevant" and realization != "not_applicable":
        raise ContractError("non-relevant privacy module requires inapplicable distribution actor realization")
    if relevance == "relevant":
        if visibility == "absent" and realization != "not_applicable":
            raise ContractError("absent distribution requires inapplicable actor realization")
        if visibility == "visible":
            if realization == "not_applicable":
                raise ContractError("visible distribution requires actor realization")
            if position == "not_visible" and realization not in OMITTED_DISTRIBUTION_ACTORS:
                raise ContractError("invisible distribution actor requires a stage-specific omission realization")
            if position != "not_visible" and realization in OMITTED_DISTRIBUTION_ACTORS:
                raise ContractError("visible distribution actor is incompatible with omission realization")
            if position == "gender_unspecified" and realization not in VISIBLE_DISTRIBUTION_ACTORS:
                raise ContractError("gender-unspecified distribution actor requires a visible actor realization")
        if visibility == "unclear" and realization != "unclear":
            raise ContractError("unclear distribution requires unclear actor realization")
    derived["distribution_actor_realization_consistent"] = True
    return derived


def validate_contract_files() -> dict[str, Any]:
    codebook = load_json(DEFAULT_CODEBOOK)
    schema = load_json(DEFAULT_SCHEMA)
    template = DEFAULT_PROMPT.read_text(encoding="utf-8")
    rendered = render_system_prompt(codebook, template)
    required = v01.required_keys(codebook)
    if set(schema.get("required", [])) != required:
        raise ContractError("schema/codebook required field sets differ")
    if set(schema.get("properties", {})) != required:
        raise ContractError("schema/codebook property field sets differ")
    if set(schema["$defs"]["position"]["enum"]) != set(codebook["common_positions"]):
        raise ContractError("position enums differ")
    if set(schema["$defs"]["actorRealization"]["enum"]) != set(codebook["actor_realizations"]):
        raise ContractError("actor realization enums differ")
    if set(schema["$defs"]["targetRealization"]["enum"]) != set(codebook["target_realizations"]):
        raise ContractError("target realization enums differ")
    if set(schema["$defs"]["distributionActorRealization"]["enum"]) != set(
        codebook["distribution_actor_realizations"]
    ):
        raise ContractError("distribution actor realization enums differ")
    if PROMPT_PLACEHOLDER in rendered:
        raise ContractError("rendered prompt contains unresolved placeholder")
    return {"codebook": codebook, "schema": schema, "rendered_prompt": rendered}
