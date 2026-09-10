"""Small, dependency-free estimators used in public result checks."""

from __future__ import annotations

import math
from typing import Any


def proportion(count: int, denominator: int) -> float:
    if denominator <= 0 or not 0 <= count <= denominator:
        raise ValueError("count and denominator are inconsistent")
    return count / denominator


def wilson_interval(count: int, denominator: int, z: float = 1.959963984540054) -> tuple[float, float]:
    """Return a two-sided Wilson score interval for a binomial proportion."""

    estimate = proportion(count, denominator)
    denominator_adjusted = 1 + z**2 / denominator
    center = (estimate + z**2 / (2 * denominator)) / denominator_adjusted
    half_width = (
        z
        * math.sqrt(
            estimate * (1 - estimate) / denominator + z**2 / (4 * denominator**2)
        )
        / denominator_adjusted
    )
    return center - half_width, center + half_width


def summarize_paired_binary(
    *, target_only: int, actor_only: int, both: int, neither: int
) -> dict[str, Any]:
    cells = (target_only, actor_only, both, neither)
    if any(not isinstance(value, int) or value < 0 for value in cells):
        raise ValueError("paired cell counts must be non-negative integers")
    eligible = sum(cells)
    target_visible = target_only + both
    actor_visible = actor_only + both
    target_rate = proportion(target_visible, eligible)
    actor_rate = proportion(actor_visible, eligible)
    return {
        "eligible_n": eligible,
        "target_visible_n": target_visible,
        "actor_visible_n": actor_visible,
        "target_visible_rate": target_rate,
        "actor_visible_rate": actor_rate,
        "paired_difference": target_rate - actor_rate,
        "target_wilson_95": wilson_interval(target_visible, eligible),
        "actor_wilson_95": wilson_interval(actor_visible, eligible),
    }
