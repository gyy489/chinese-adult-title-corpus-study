"""Five-label contract for the independent 53rd privacy-router benchmark."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CODEBOOK = ROOT / "config/gendered_privacy_router_codebook_v1.json"
DEFAULT_SCHEMA = ROOT / "config/gendered_privacy_router_output_schema_v1.json"
DEFAULT_PROMPT = ROOT / "prompts/gendered_privacy_router_model_benchmark_v1.md"
PROMPT_PLACEHOLDER = "{{CODEBOOK_JSON}}"
FIELDS = ("rv", "privacy", "creation", "distribution", "exposure")
ITEM_KEYS = {"i", *FIELDS}
STAGES = ("creation", "distribution", "exposure")


class ContractError(ValueError):
    """Raised when an output violates the frozen router contract."""


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ContractError(f"{path} must contain an object")
    return value


def render_system_prompt(codebook: dict[str, Any]) -> str:
    template = DEFAULT_PROMPT.read_text(encoding="utf-8")
    if template.count(PROMPT_PLACEHOLDER) != 1:
        raise ContractError("prompt must contain one codebook placeholder")
    compact = json.dumps(codebook, ensure_ascii=False, separators=(",", ":"))
    return template.replace(PROMPT_PLACEHOLDER, compact)


def validate_item(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict) or set(item) != ITEM_KEYS:
        raise ContractError("item must contain exactly i/rv/privacy/creation/distribution/exposure")
    if not isinstance(item["i"], int) or isinstance(item["i"], bool) or item["i"] < 1:
        raise ContractError("i must be a positive integer")
    allowed = {
        "rv": {"valid", "technical_noise", "unknown"},
        "privacy": {"relevant", "not_relevant", "unclear", "not_applicable"},
        "creation": {"visible", "absent", "unclear", "not_applicable"},
        "distribution": {"visible", "absent", "unclear", "not_applicable"},
        "exposure": {"visible", "absent", "unclear", "not_applicable"},
    }
    for field in FIELDS:
        if item[field] not in allowed[field]:
            raise ContractError(f"{field} has an invalid value")
    if item["rv"] != "valid":
        if any(item[field] != "not_applicable" for field in FIELDS if field != "rv"):
            raise ContractError("non-valid record requires all router fields not_applicable")
    elif item["privacy"] == "not_relevant":
        if any(item[field] != "not_applicable" for field in STAGES):
            raise ContractError("not_relevant requires all stages not_applicable")
    elif item["privacy"] == "relevant":
        if any(item[field] == "not_applicable" for field in STAGES):
            raise ContractError("relevant privacy chain cannot use not_applicable stages")
    elif item["privacy"] == "unclear":
        if any(item[field] != "unclear" for field in STAGES):
            raise ContractError("unclear privacy relevance requires all stages unclear")
    else:
        raise ContractError("valid record cannot use privacy=not_applicable")
    return item


def compact_reference(output: dict[str, Any], item_index: int) -> dict[str, Any]:
    if not isinstance(output, dict):
        raise ContractError("source output must be an object")
    item = {
        "i": int(item_index),
        "rv": output["record_validity"],
        "privacy": output["privacy_chain_relevance"],
        "creation": output["material_creation_visibility"],
        "distribution": output["distribution_visibility"],
        "exposure": output["exposure_visibility"],
    }
    return validate_item(item)


def expand_item(item: dict[str, Any]) -> dict[str, Any]:
    validate_item(item)
    return {
        "record_validity": item["rv"],
        "privacy_chain_relevance": item["privacy"],
        "material_creation_visibility": item["creation"],
        "distribution_visibility": item["distribution"],
        "exposure_visibility": item["exposure"],
    }


def validate_response(value: Any, expected_indices: set[int]) -> list[dict[str, Any]]:
    if not isinstance(value, dict) or set(value) != {"items"}:
        raise ContractError("response must contain exactly top-level items")
    items = value["items"]
    if not isinstance(items, list):
        raise ContractError("items must be an array")
    indices = [item.get("i") if isinstance(item, dict) else None for item in items]
    if len(indices) != len(set(indices)) or set(indices) != expected_indices:
        raise ContractError("response index set differs from request")
    return sorted((validate_item(item) for item in items), key=lambda item: item["i"])


def validate_contract_files() -> dict[str, Any]:
    codebook = load_json(DEFAULT_CODEBOOK)
    schema = load_json(DEFAULT_SCHEMA)
    rendered = render_system_prompt(codebook)
    if codebook.get("version") != "1.0.0":
        raise ContractError("unexpected codebook version")
    if tuple(codebook.get("fields", {})) != FIELDS:
        raise ContractError("codebook fields differ from contract")
    item_schema = schema.get("$defs", {}).get("item", {})
    if set(item_schema.get("required", [])) != ITEM_KEYS:
        raise ContractError("schema required fields differ from contract")
    if set(item_schema.get("properties", {})) != ITEM_KEYS:
        raise ContractError("schema properties differ from contract")
    if PROMPT_PLACEHOLDER in rendered:
        raise ContractError("rendered prompt contains unresolved placeholder")
    return {"codebook": codebook, "schema": schema, "rendered_prompt": rendered}
