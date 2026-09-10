"""Aggregate two-coder and human-versus-AI categorical agreement."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any


class ReviewError(ValueError):
    """Raised when review records are incomplete or duplicated."""


def _index(
    rows: Iterable[dict[str, Any]], *, item_field: str, label_field: str
) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in rows:
        item = str(row[item_field])
        if item in result:
            raise ReviewError(f"duplicate review item: {item}")
        result[item] = str(row[label_field])
    return result


def confusion_matrix(
    left: dict[str, str], right: dict[str, str]
) -> dict[str, dict[str, int]]:
    if set(left) != set(right):
        raise ReviewError("review sets do not contain the same items")
    labels = sorted(set(left.values()) | set(right.values()))
    return {
        left_label: {
            right_label: sum(
                left[item] == left_label and right[item] == right_label for item in left
            )
            for right_label in labels
        }
        for left_label in labels
    }


def cohen_kappa(left: dict[str, str], right: dict[str, str]) -> float:
    if set(left) != set(right) or not left:
        raise ReviewError("kappa requires the same non-empty item set")
    size = len(left)
    observed = sum(left[item] == right[item] for item in left) / size
    left_counts = Counter(left.values())
    right_counts = Counter(right.values())
    labels = set(left_counts) | set(right_counts)
    expected = sum(left_counts[label] * right_counts[label] for label in labels) / size**2
    if expected == 1:
        return 1.0 if observed == 1 else 0.0
    return (observed - expected) / (1 - expected)


def compare(
    left_rows: Iterable[dict[str, Any]],
    right_rows: Iterable[dict[str, Any]],
    *,
    item_field: str = "item_id",
    label_field: str = "label",
) -> dict[str, Any]:
    left = _index(left_rows, item_field=item_field, label_field=label_field)
    right = _index(right_rows, item_field=item_field, label_field=label_field)
    matrix = confusion_matrix(left, right)
    agreement_n = sum(left[item] == right[item] for item in left)
    return {
        "n": len(left),
        "exact_agreement_n": agreement_n,
        "exact_agreement_rate": agreement_n / len(left),
        "cohen_kappa": cohen_kappa(left, right),
        "confusion": matrix,
    }
