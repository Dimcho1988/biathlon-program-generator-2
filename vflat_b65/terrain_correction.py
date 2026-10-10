"""Fixed session terrain correction validated on NordicSki/Walk recordings.

Provider ascent and distance are authoritative. Do not substitute ascent
reconstructed from noisy altitude, extrapolate, or fit individual coefficients.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np
import pandas as pd

SLOPE = 0.8834239445414596
INTERCEPT = -7.974288128873758
DENSITY_RANGE = (6.177543912583202, 29.354843998838497)
SPORTS = frozenset(("NordicSki", "Walk"))


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def terrain_metadata(detail: Mapping[str, Any] | None) -> dict[str, Any]:
    """Only provider fields affecting the correction and its cache identity."""
    detail = detail or {}
    sport = detail.get("type")
    return {
        "sport": sport if isinstance(sport, str) else None,
        "distance_m": _number(detail.get("distance")),
        "elevation_gain_m": _number(detail.get("total_elevation_gain")),
    }


def terrain_correction(frame: pd.DataFrame, detail: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return one session multiplier; all original sample eligibility survives."""
    metadata = terrain_metadata(detail)
    result = {
        **metadata, "factor": 1.0, "applied": False, "reason": None,
        "gain_per_km": None, "predicted_excess_pct": 0.0,
        "uphill_only": None, "uphill_time_share": None, "downhill_time_share": None,
        "slope": SLOPE, "intercept": INTERCEPT,
        "density_range_m_per_km": list(DENSITY_RANGE),
    }
    if metadata["sport"] not in SPORTS:
        return {**result, "reason": "SPORT_NOT_VALIDATED"}
    distance, gain = metadata["distance_m"], metadata["elevation_gain_m"]
    if distance is None or distance <= 0 or gain is None or gain < 0:
        return {**result, "reason": "TERRAIN_METADATA_MISSING"}
    density = gain / (distance / 1000.0)
    if not math.isfinite(density):
        return {**result, "reason": "TERRAIN_METADATA_INVALID"}
    result["gain_per_km"] = density
    if not {"block", "grade_pct", "speed_raw_mps", "vflat_b65_kmh"}.issubset(frame):
        return {**result, "reason": "TERRAIN_SAMPLES_MISSING"}
    if frame.empty:
        return {**result, "reason": "TERRAIN_SAMPLES_MISSING"}
    if "timestamp" in frame:
        stamps = pd.to_datetime(frame.timestamp, utc=True, errors="coerce")
        if stamps.isna().any():
            return {**result, "reason": "TERRAIN_SAMPLES_MISSING"}
        times = (stamps - stamps.iloc[0]).dt.total_seconds().to_numpy()
    elif "elapsed_s" in frame:
        times = frame.elapsed_s.to_numpy(dtype=float)
    else:
        return {**result, "reason": "TERRAIN_SAMPLES_MISSING"}
    if not len(times) or not np.isfinite(times).all():
        return {**result, "reason": "TERRAIN_SAMPLES_MISSING"}
    block = frame.block.to_numpy()
    dt = np.r_[0.0, np.diff(times)]
    continuous = np.r_[False, block[1:] == block[:-1]] & (block != -1)
    dt = np.where(continuous & (dt > 0) & (dt <= 1.0), dt, 0.0)
    speed = frame.speed_raw_mps.to_numpy(dtype=float) * 3.6
    grade = frame.grade_pct.to_numpy(dtype=float)
    use = (dt > 0) & np.isfinite(speed) & np.isfinite(grade) & np.isfinite(frame.vflat_b65_kmh.to_numpy(dtype=float))
    # Match the calibration: remove <=1 km/h stops lasting >=3 elapsed seconds.
    # Recording gaps split stop episodes as well as the time weights.
    breaks = np.r_[0, np.flatnonzero((~continuous[1:]) | (np.diff(times) > 1.0)) + 1, len(frame)]
    for left, right in zip(breaks[:-1], breaks[1:]):
        stopped = speed[left:right] <= 1.0
        edges = np.diff(np.r_[False, stopped, False].astype(int))
        for start, end in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)):
            start, end = left + start, left + end
            if times[end - 1] - times[start] >= 3.0:
                use[start:end] = False
    seconds = float(dt[use].sum())
    if seconds <= 0:
        return {**result, "reason": "TERRAIN_SAMPLES_MISSING"}
    up = float(dt[use & (grade > 1.0)].sum() / seconds)
    down = float(dt[use & (grade < -1.0)].sum() / seconds)
    uphill = up >= 0.8 and down < 0.1
    result.update(uphill_time_share=up, downhill_time_share=down, uphill_only=uphill)
    if uphill:
        return {**result, "reason": "UPHILL_ONLY"}
    if not DENSITY_RANGE[0] <= density <= DENSITY_RANGE[1]:
        return {**result, "reason": "OUTSIDE_VALIDATED_RANGE"}
    excess = max(0.0, SLOPE * density + INTERCEPT)
    return {
        **result, "predicted_excess_pct": excess,
        "factor": 1.0 / (1.0 + excess / 100.0), "applied": excess > 0,
        "reason": "APPLIED" if excess > 0 else "BELOW_CORRECTION_THRESHOLD",
    }
