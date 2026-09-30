"""Explicit expert HRmax schemes; no population preset or inferred HRmax."""
from __future__ import annotations

import math


def validate_percentages(values) -> tuple[float, ...]:
    percentages = tuple(float(value) for value in values)
    if (
        len(percentages) != 6
        or any(not math.isfinite(value) or not 0 < value <= 100 for value in percentages)
        or any(a >= b for a, b in zip(percentages, percentages[1:]))
        or percentages[-1] != 100
    ):
        raise ValueError("six increasing HRmax percentages ending at 100 are required")
    return percentages


def bounds_from_hrmax(hrmax_bpm: int, percentages) -> tuple[int, ...]:
    if isinstance(hrmax_bpm, bool) or not isinstance(hrmax_bpm, int) or not 30 <= hrmax_bpm <= 240:
        raise ValueError("an explicit HRmax between 30 and 240 bpm is required")
    scheme = validate_percentages(percentages)
    # Round halves upward consistently with the settings preview.
    bounds = tuple(math.floor(hrmax_bpm * value / 100 + 0.5) for value in scheme)
    if bounds[0] < 30 or any(a >= b for a, b in zip(bounds, bounds[1:])):
        raise ValueError("the HRmax scheme must produce six increasing boundaries of at least 30 bpm")
    return bounds
