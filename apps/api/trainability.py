"""Duration-weighted rank index over immutable HRmod and Vflat outputs."""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

import numpy as np

MODEL_VERSION = "trainability_rank_v1"
MIN_SECONDS = 60.0
MIN_ACTIVITY_SECONDS = 420.0
GENERAL_RANGE = (0.75, 0.92)
MIN_GRADE_PCT = -3.0


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def compute_trainability(
    hr_rows: Sequence[Mapping[str, Any]],
    speed_rows: Sequence[Mapping[str, Any]],
    *,
    zone_bounds_bpm: Sequence[int],
    hrmax_bpm: int | None,
    activity_duration_s: float | None,
    comparison_key: str,
    source_versions: Mapping[str, str],
) -> dict[str, Any]:
    """Allocate the complete eligible speed distribution before validating bands.

    HR weights are the existing HRmod dt_s, including its gap semantics. Vflat
    weights are active 1 Hz intervals supplied by the adapter. Do not use the
    combined shadow exclusion flag: an uncorrected HR wave is still valid HR.
    """
    hrs = [
        (hr, dt)
        for row in hr_rows
        if (hr := _number(row.get("hrmod_final_bpm"))) is not None
        and (dt := _number(row.get("dt_s"))) is not None and dt > 0
    ]
    hr_values = np.asarray([value for value, _ in hrs], dtype=float)
    hr_weights = np.asarray([dt for _, dt in hrs], dtype=float)
    hr_total = float(hr_weights.sum())
    speeds: list[tuple[float, float]] = []
    downhill_seconds = unavailable_seconds = 0.0
    for row in speed_rows:
        dt = _number(row.get("dt_s"))
        if dt is None or dt <= 0:
            continue
        speed = _number(row.get("vflat_b65_kmh"))
        # This is the unclipped, spatially smoothed grade actually used by Vflat.
        grade = _number(row.get("grade_raw_pct"))
        if speed is None or speed < 0 or grade is None or row.get("exclusion_reason"):
            unavailable_seconds += dt
        elif grade < MIN_GRADE_PCT:
            downhill_seconds += dt
        else:
            speeds.append((speed, dt))
    speeds.sort(key=lambda pair: pair[0], reverse=True)
    values = np.asarray([value for value, _ in speeds], dtype=float)
    weights = np.asarray([dt for _, dt in speeds], dtype=float)
    ends = np.cumsum(weights)
    starts = ends - weights
    speed_total = float(weights.sum())

    def band(name: str, lower: float | None, upper: float | None, inclusive: bool):
        mask = np.zeros(len(hrs), dtype=bool)
        above = mask.copy()
        if lower is not None and upper is not None:
            above = hr_values > upper if inclusive else hr_values >= upper
            mask = (hr_values >= lower) & ~above
        seconds = float(hr_weights[mask].sum())
        share = seconds / hr_total if hr_total > 0 else 0.0
        rank_start = float(hr_weights[above].sum()) / hr_total if hr_total > 0 else 0.0
        left, right = rank_start * speed_total, (rank_start + share) * speed_total
        overlap = np.maximum(0.0, np.minimum(ends, right) - np.maximum(starts, left))
        allocated = float(overlap.sum())
        hr_mean = float(np.dot(hr_values[mask], hr_weights[mask]) / seconds) if seconds else None
        speed_mean = float(np.dot(values, overlap) / allocated) if allocated else None
        reason = None
        if activity_duration_s is None:
            reason = "ACTIVITY_DURATION_MISSING"
        elif activity_duration_s < MIN_ACTIVITY_SECONDS:
            reason = "ACTIVITY_BELOW_7MIN"
        elif hrmax_bpm is None:
            reason = "HRMAX_MISSING"
        elif seconds + 1e-9 < MIN_SECONDS:
            reason = "HR_TIME_BELOW_60S"
        elif allocated + 1e-9 < MIN_SECONDS:
            reason = "SPEED_TIME_BELOW_60S"
        elif speed_mean is None or speed_mean <= 0:
            reason = "ZERO_SPEED"
        return {
            "name": name, "lower_bpm": lower, "upper_bpm": upper,
            "hr_seconds": seconds, "hr_percent": share * 100,
            "speed_seconds": allocated,
            "mean_hrmod_bpm": hr_mean,
            "mean_hrmax_percent": hr_mean / hrmax_bpm * 100 if hr_mean is not None and hrmax_bpm else None,
            "mean_vflat_kmh": speed_mean,
            "index": hr_mean / speed_mean if reason is None else None,
            "valid": reason is None, "invalid_reason": reason,
        }

    bounds = [float(value) for value in zone_bounds_bpm]
    if len(bounds) != 6 or any(a >= b for a, b in zip(bounds, bounds[1:])):
        raise ValueError("Trainability requires the existing five HR zones")
    if hrmax_bpm is not None and hrmax_bpm <= 0:
        raise ValueError("HRmax must be positive")
    return {
        "schema_version": "trainability-index-v1", "model_version": MODEL_VERSION,
        "comparison_key": comparison_key, "source_versions": dict(source_versions),
        "hrmax_bpm": hrmax_bpm, "zone_bounds_bpm": bounds,
        "activity_duration_s": activity_duration_s,
        "minimum_activity_seconds": MIN_ACTIVITY_SECONDS,
        "minimum_seconds": MIN_SECONDS, "minimum_grade_pct": MIN_GRADE_PCT,
        "general_range_percent": [75, 92],
        "hr_seconds": hr_total, "eligible_speed_seconds": speed_total,
        "downhill_excluded_seconds": downhill_seconds,
        "unavailable_speed_seconds": unavailable_seconds,
        "zones": [band(f"Z{i + 1}", bounds[i], bounds[i + 1], i == 4) for i in range(5)],
        "general": band("GENERAL", *(tuple(hrmax_bpm * p for p in GENERAL_RANGE) if hrmax_bpm else (None, None)), True),
    }
