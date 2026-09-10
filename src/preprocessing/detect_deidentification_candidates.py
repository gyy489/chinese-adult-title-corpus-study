"""Public interface for the production identifier-candidate detector.

The production detector combines structural rules, two local NER models, and
researcher-maintained literal person/organization dictionaries. Those literal
dictionaries are derived from restricted title text and are intentionally not
published. This module preserves the production API and the shareable
structural/NER layer so downstream audit code remains inspectable and runnable
on synthetic or independently supplied data.

This public implementation is therefore not a drop-in anonymity certification.
A real-corpus rerun must inject the restricted dictionary layer inside the
controlled research environment and retain candidate rows privately.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

RULE_VERSION = "v3.81-public-structural-interface"
DEFAULT_NER_MODELS = ("zh_core_web_sm", "zh_core_web_lg")

# Kept empty by design. The populated production list is restricted.
KNOWN_RECURRING_ALIASES: tuple[str, ...] = ()

EXPLICIT_HANDLE_RE = re.compile(
    r"(?<![\w@])@[A-Za-z0-9_][A-Za-z0-9_.-]{2,31}"
)


@dataclass(frozen=True)
class Candidate:
    record_id: str
    source_site: str
    data_round: str
    text_layer: str
    source_text: str
    signal_type: str
    matched_text: str
    span_start: int
    span_end: int
    suggested_replacement: str
    confidence: str
    note: str


def _ner_candidates(
    row: dict[str, str], text_layer: str, text: str, entities: Sequence[object]
) -> list[Candidate]:
    candidates: list[Candidate] = []
    for entity in entities:
        label = str(getattr(entity, "label_", ""))
        if label not in {"PERSON", "PER", "ORG"}:
            continue
        start = int(getattr(entity, "start_char"))
        end = int(getattr(entity, "end_char"))
        matched = text[start:end]
        if not matched.strip():
            continue
        is_person = label in {"PERSON", "PER"}
        candidates.append(
            Candidate(
                record_id=row.get("record_id", ""),
                source_site=row.get("source_site", ""),
                data_round=row.get("data_round", ""),
                text_layer=text_layer,
                source_text=text,
                signal_type="local_ner_person" if is_person else "local_ner_org",
                matched_text=matched,
                span_start=start,
                span_end=end,
                suggested_replacement="某人" if is_person else "某机构",
                confidence="review_required",
                note="Public structural/NER layer; literal dictionaries are restricted.",
            )
        )
    return candidates


def _structural_candidates(
    row: dict[str, str], text_layer: str, text: str
) -> list[Candidate]:
    candidates: list[Candidate] = []
    for match in EXPLICIT_HANDLE_RE.finditer(text):
        candidates.append(
            Candidate(
                record_id=row.get("record_id", ""),
                source_site=row.get("source_site", ""),
                data_round=row.get("data_round", ""),
                text_layer=text_layer,
                source_text=text,
                signal_type="explicit_at_handle",
                matched_text=match.group(0),
                span_start=match.start(),
                span_end=match.end(),
                suggested_replacement="某账号",
                confidence="strong",
                note="Direct locator form detected without a private literal dictionary.",
            )
        )
    return candidates


def _merge(candidates: Iterable[Candidate]) -> list[Candidate]:
    grouped: dict[tuple[str, str, int, int, str, str], list[Candidate]] = {}
    for candidate in candidates:
        key = (
            candidate.record_id,
            candidate.text_layer,
            candidate.span_start,
            candidate.span_end,
            candidate.matched_text,
            candidate.suggested_replacement,
        )
        grouped.setdefault(key, []).append(candidate)
    merged: list[Candidate] = []
    for members in grouped.values():
        signals = sorted({member.signal_type for member in members})
        confidence = (
            "strong"
            if any(member.confidence == "strong" for member in members)
            else "review_required"
        )
        merged.append(
            replace(
                members[0], signal_type="|".join(signals), confidence=confidence
            )
        )
    return sorted(
        merged,
        key=lambda item: (
            item.record_id,
            item.text_layer,
            item.span_start,
            item.span_end,
            item.signal_type,
        ),
    )


def detect_candidates(
    rows: Iterable[dict[str, str]],
    *,
    nlp=None,
    nlp_models: Sequence[object] = (),
    text_layers: Sequence[str] = ("cleaned_title",),
) -> list[Candidate]:
    """Run the public structural/NER layer over caller-supplied rows."""

    models = list(nlp_models) if nlp_models else ([nlp] if nlp is not None else [])
    candidates: list[Candidate] = []
    for row in rows:
        for text_layer in text_layers:
            text = row.get(text_layer, "") or ""
            if not text:
                continue
            candidates.extend(_structural_candidates(row, text_layer, text))
            for model in models:
                doc = model(text)
                candidates.extend(_ner_candidates(row, text_layer, text, list(doc.ents)))
    return _merge(candidates)


def load_ner_model(model_name: str):
    try:
        import spacy
    except ImportError as exc:  # pragma: no cover - optional heavyweight layer
        raise RuntimeError("spaCy is required for the optional local NER layer") from exc
    try:
        model = spacy.load(model_name)
    except OSError as exc:  # pragma: no cover - depends on local model install
        raise RuntimeError(f"spaCy model {model_name!r} is not installed") from exc
    model.select_pipes(
        enable=[name for name in ("tok2vec", "ner") if name in model.pipe_names]
    )
    return model


def load_ner_models(model_names: Sequence[str]) -> list[object]:
    return [load_ner_model(name) for name in model_names]
