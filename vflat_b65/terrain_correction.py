"""Session terrain diagnostics; grade correction lives in the sample model.

The experimental September 2026 density regression reduced every sample.
It was not consistently validated across athletes and is retired.
Provider totals remain visible for audit but never rescale the speed curve.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

import pandas as pd

def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def terrain_metadata(detail: Mapping[str, Any] | None) -> dict[str, Any]:
    """Provider totals retained in diagnostics and the recording fingerprint."""
    detail = detail or {}
    sport = detail.get("type")
    return {
        "sport": sport if isinstance(sport, str) else None,
        "distance_m": _number(detail.get("distance")),
        "elevation_gain_m": _number(detail.get("total_elevation_gain")),
    }


def terrain_correction(frame: pd.DataFrame, detail: Mapping[str, Any] | None) -> dict[str, Any]:
    """Preserve the diagnostic contract with a neutral session multiplier."""
    metadata = terrain_metadata(detail)
    distance, gain = metadata["distance_m"], metadata["elevation_gain_m"]
    density = gain / (distance / 1000.0) if distance and distance > 0 and gain is not None and gain >= 0 else None
    return {**metadata, "factor": 1.0, "applied": False,
            "reason": "SESSION_TERRAIN_CORRECTION_DISABLED",
            "gain_per_km": density if density is not None and math.isfinite(density) else None,
            "predicted_excess_pct": 0.0}
