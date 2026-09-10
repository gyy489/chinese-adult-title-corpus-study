"""Compact contract for the 52nd low-cost DeepSeek model benchmark.

The benchmark is a bridge test against frozen v0.3 machine annotations.  It is
deliberately smaller than the manuscript contract and must never be described
as a human-gold accuracy evaluation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CODEBOOK = (
    ROOT / "config/gendered_argument_visibility_benchmark_codebook_v1.json"
)
DEFAULT_SCHEMA = (
    ROOT / "config/gendered_argument_visibility_benchmark_output_schema_v1.json"
)
DEFAULT_PROMPT = (
    ROOT / "prompts/gendered_argument_visibility_model_benchmark_v1.md"
)
PROMPT_PLACEHOLDER = "{{CODEBOOK_JSON}}"

LABEL_FIELDS = (
    "rv",
    "cv",
    "ct",
    "dv",
    "da",
    "dr",
    "dt",
    "au",
    "ev",
    "et",
    "ob",
    "de",
    "pl",
    "rr",
    "sp",
)
QUALITY_FIELDS = ("ar", "e")
ALL_FIELDS = LABEL_FIELDS + QUALITY_FIELDS
VISIBLE_ACTOR_REALIZATIONS = {"eg", "eu", "gc", "za"}
OMITTED_ACTOR_REALIZATIONS = {"po", "ne", "no"}
NO_EVIDENCE_LONG_VALUES = {
    "absent",
    "not_visible",
    "not_applicable",
    "no_visible_authorization_language",
    "no_actor_expression",
}


class ContractError(ValueError):
    """Raised when a compact response violates the frozen benchmark contract."""


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ContractError(f"{path} must contain a JSON object")
    return value


def render_system_prompt(
    codebook: dict[str, Any], template: str | None = None
) -> str:
    value = template if template is not None else DEFAULT_PROMPT.read_text(encoding="utf-8")
    if value.count(PROMPT_PLACEHOLDER) != 1:
        raise ContractError("prompt must contain exactly one codebook placeholder")
    compact = json.dumps(codebook, ensure_ascii=False, separators=(",", ":"))
    return value.replace(PROMPT_PLACEHOLDER, compact)


def _field_spec(codebook: dict[str, Any], field: str) -> dict[str, Any]:
    try:
        return codebook["fields"][field]
    except KeyError as exc:
        raise ContractError(f"unknown compact field: {field}") from exc


def enum_map(codebook: dict[str, Any], field: str) -> dict[str, str]:
    spec = _field_spec(codebook, field)
    if "enum" in spec:
        values = spec["enum"]
    elif "enum_ref" in spec:
        values = codebook["enums"][spec["enum_ref"]]
    else:
        raise ContractError(f"field {field} has no enum")
    if not isinstance(values, dict) or not values:
        raise ContractError(f"field {field} enum must be a non-empty mapping")
    return {str(key): str(value) for key, value in values.items()}


def long_field(codebook: dict[str, Any], field: str) -> str:
    value = _field_spec(codebook, field).get("long")
    if not isinstance(value, str) or not value:
        raise ContractError(f"field {field} has no long name")
    return value


def short_field_map(codebook: dict[str, Any]) -> dict[str, str]:
    return {field: long_field(codebook, field) for field in ALL_FIELDS}


def long_to_short_fields(codebook: dict[str, Any]) -> dict[str, str]:
    forward = short_field_map(codebook)
    reverse = {value: key for key, value in forward.items()}
    if len(reverse) != len(forward):
        raise ContractError("long field names must be unique")
    return reverse


def expand_code(codebook: dict[str, Any], field: str, code: Any) -> str:
    if not isinstance(code, str) or code not in enum_map(codebook, field):
        raise ContractError(f"{field} has invalid compact code: {code!r}")
    return enum_map(codebook, field)[code]


def compact_code(codebook: dict[str, Any], field: str, code: Any) -> str:
    reverse = {value: key for key, value in enum_map(codebook, field).items()}
    if not isinstance(code, str) or code not in reverse:
        raise ContractError(f"{long_field(codebook, field)} has unknown code: {code!r}")
    return reverse[code]


def compact_output(
    output: dict[str, Any],
    codebook: dict[str, Any],
    *,
    item_index: int,
    title: str | None = None,
) -> dict[str, Any]:
    """Deterministically reduce a frozen v0.3 output to the 17-field bridge."""
    if not isinstance(output, dict):
        raise ContractError("source output must be an object")
    result: dict[str, Any] = {"i": int(item_index)}
    short_by_long = long_to_short_fields(codebook)
    for field in LABEL_FIELDS:
        source = long_field(codebook, field)
        if source not in output:
            raise ContractError(f"source output lacks {source}")
        result[field] = compact_code(codebook, field, output[source])
    reasons = output.get("adjudication_reasons")
    if not isinstance(reasons, list):
        raise ContractError("source adjudication_reasons must be an array")
    result["ar"] = list(reasons)
    evidence: list[dict[str, Any]] = []
    for row in output.get("evidence", []):
        if not isinstance(row, dict) or not isinstance(row.get("supports"), list):
            continue
        supports: list[list[str]] = []
        for support in row["supports"]:
            if not isinstance(support, dict):
                continue
            short = short_by_long.get(support.get("field"))
            if short not in LABEL_FIELDS or short == "rv":
                continue
            try:
                short_value = compact_code(codebook, short, support.get("code"))
            except ContractError:
                continue
            if (
                result[short] == short_value
                and expand_code(codebook, short, short_value)
                not in NO_EVIDENCE_LONG_VALUES
            ):
                supports.append([short, short_value])
        deduplicated = sorted({tuple(pair) for pair in supports})
        if (
            deduplicated
            and isinstance(row.get("span"), str)
            and (title is None or row["span"] in title)
        ):
            evidence.append(
                {"s": row["span"], "q": [list(pair) for pair in deduplicated]}
            )
    result["e"] = evidence
    return result


def expand_item(item: dict[str, Any], codebook: dict[str, Any]) -> dict[str, Any]:
    """Expand the compact item into long v0.3-compatible field names."""
    expanded: dict[str, Any] = {}
    for field in LABEL_FIELDS:
        expanded[long_field(codebook, field)] = expand_code(codebook, field, item[field])
    expanded["adjudication_reasons"] = list(item["ar"])
    evidence: list[dict[str, Any]] = []
    for row in item["e"]:
        evidence.append(
            {
                "span": row["s"],
                "supports": [
                    {
                        "field": long_field(codebook, field),
                        "code": expand_code(codebook, field, code),
                    }
                    for field, code in row["q"]
                ],
            }
        )
    expanded["evidence"] = evidence
    return expanded


def _validate_stage(item: dict[str, Any], visibility: str, target: str) -> None:
    stage = item[visibility]
    position = item[target]
    if stage == "a" and position != "n":
        raise ContractError(f"{visibility}=a requires {target}=n")
    if stage == "na" and position != "na":
        raise ContractError(f"{visibility}=na requires {target}=na")
    if stage == "q" and position != "q":
        raise ContractError(f"{visibility}=q requires {target}=q")
    if stage == "v" and position == "na":
        raise ContractError(f"{visibility}=v is incompatible with {target}=na")


def _expected_evidence_pairs(
    item: dict[str, Any], codebook: dict[str, Any]
) -> set[tuple[str, str]]:
    expected: set[tuple[str, str]] = set()
    for field in LABEL_FIELDS:
        if field == "rv":
            continue
        long_value = expand_code(codebook, field, item[field])
        if long_value not in NO_EVIDENCE_LONG_VALUES:
            expected.add((field, item[field]))
    return expected


def validate_compact_item(
    item: dict[str, Any],
    title: str,
    codebook: dict[str, Any],
    *,
    require_complete_evidence: bool = True,
) -> dict[str, Any]:
    """Validate structure, dependencies, exact spans, and evidence coverage."""
    if not isinstance(item, dict):
        raise ContractError("benchmark item must be an object")
    expected_keys = {"i", *ALL_FIELDS}
    if set(item) != expected_keys:
        raise ContractError(
            f"item fields differ; missing={sorted(expected_keys - set(item))}, "
            f"extra={sorted(set(item) - expected_keys)}"
        )
    if not isinstance(item["i"], int) or isinstance(item["i"], bool) or item["i"] < 1:
        raise ContractError("i must be a positive integer")
    for field in LABEL_FIELDS:
        expand_code(codebook, field, item[field])
    reasons = item["ar"]
    if not isinstance(reasons, list) or len(reasons) != len(set(reasons)) or any(
        not isinstance(reason, str)
        or not reason.strip()
        or len(reason) > 240
        for reason in reasons
    ):
        raise ContractError("ar must contain unique 1-240 character strings")
    has_unclear = any(item[field] == "q" for field in LABEL_FIELDS)
    if has_unclear and not reasons:
        raise ContractError("every q code requires a reason in ar")
    if item["rv"] != "v":
        if any(item[field] != "na" for field in LABEL_FIELDS if field != "rv"):
            raise ContractError("non-valid records require all annotation fields=na")
        if item["e"]:
            raise ContractError("non-valid records require e=[]")
    else:
        _validate_stage(item, "cv", "ct")
        _validate_stage(item, "ev", "et")
        distribution = item["dv"]
        if distribution == "a" and (item["da"], item["dr"], item["dt"], item["au"]) != (
            "n",
            "na",
            "n",
            "na",
        ):
            raise ContractError("dv=a requires da=n, dr=na, dt=n, au=na")
        if distribution == "na" and any(
            item[field] != "na" for field in ("da", "dr", "dt", "au")
        ):
            raise ContractError("dv=na requires da/dr/dt/au=na")
        if distribution == "q" and "q" not in {
            item["da"], item["dr"], item["dt"], item["au"]
        }:
            raise ContractError("dv=q requires an unclear distribution argument")
        if distribution == "v":
            if item["da"] in {"na"} or item["dt"] == "na" or item["au"] == "na":
                raise ContractError("dv=v is incompatible with inapplicable arguments")
            if item["da"] == "n" and item["dr"] not in OMITTED_ACTOR_REALIZATIONS:
                raise ContractError("dv=v,da=n requires dr=po/ne/no")
            if item["da"] not in {"n", "q"} and item["dr"] not in VISIBLE_ACTOR_REALIZATIONS:
                raise ContractError("visible da requires dr=eg/eu/gc/za")
            if item["da"] == "q" and item["dr"] != "q":
                raise ContractError("da=q requires dr=q")

    evidence = item["e"]
    if not isinstance(evidence, list):
        raise ContractError("e must be an array")
    expected_pairs = _expected_evidence_pairs(item, codebook)
    seen: set[tuple[str, str]] = set()
    for index, row in enumerate(evidence):
        if not isinstance(row, dict) or set(row) != {"s", "q"}:
            raise ContractError(f"e[{index}] must contain exactly s/q")
        span = row["s"]
        if not isinstance(span, str) or not span or len(span) > 160 or span not in title:
            raise ContractError(f"e[{index}].s must be an exact title substring")
        supports = row["q"]
        if not isinstance(supports, list) or not supports:
            raise ContractError(f"e[{index}].q must be a non-empty array")
        for support in supports:
            if (
                not isinstance(support, list)
                or len(support) != 2
                or not all(isinstance(value, str) for value in support)
            ):
                raise ContractError("each evidence support must be [field,code]")
            field, code = support
            if field not in LABEL_FIELDS or field == "rv":
                raise ContractError(f"unsupported evidence field: {field!r}")
            expand_code(codebook, field, code)
            pair = (field, code)
            if item[field] != code or pair not in expected_pairs:
                raise ContractError(f"evidence support does not match selected code: {pair}")
            seen.add(pair)
    missing = expected_pairs - seen
    if require_complete_evidence and missing:
        raise ContractError(f"selected codes lack evidence: {sorted(missing)}")
    return {
        "contract_valid": True,
        "evidence_complete": not missing,
        "missing_evidence_pairs": sorted(missing),
        "expanded": expand_item(item, codebook),
    }


def validate_response(
    value: Any, titles: dict[int, str], codebook: dict[str, Any]
) -> list[dict[str, Any]]:
    if not isinstance(value, dict) or set(value) != {"items"}:
        raise ContractError("response must contain exactly top-level items")
    items = value["items"]
    if not isinstance(items, list):
        raise ContractError("items must be an array")
    indices = [item.get("i") if isinstance(item, dict) else None for item in items]
    if len(indices) != len(set(indices)):
        raise ContractError("response contains duplicate indices")
    if set(indices) != set(titles):
        raise ContractError(
            f"response index set differs; missing={sorted(set(titles) - set(indices))}, "
            f"extra={sorted(set(indices) - set(titles))}"
        )
    for item in items:
        validate_compact_item(item, titles[item["i"]], codebook)
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
    if PROMPT_PLACEHOLDER in rendered:
        raise ContractError("rendered prompt contains unresolved placeholder")
    for field in LABEL_FIELDS:
        mapping = enum_map(codebook, field)
        if len(mapping) != len(set(mapping.values())):
            raise ContractError(f"field {field} enum expansion is not one-to-one")
    return {"codebook": codebook, "schema": schema, "rendered_prompt": rendered}
