"""Duration-weighted heart-rate zone diagnostics over normalized intervals.

The calculation is independent of Intervals.icu, Streamlit, HTTP, storage,
and 1 Hz materialization.  It consumes the lightweight interval-aware result
and externally supplied, validated zone definitions.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import math
from typing import Any

from intervals_inspector.stream_normalizer import IntervalAwareResult


ALGORITHM_VERSION = "hr-zone-time-interval-aware-v1"
_ACTIVE_CLASSIFICATIONS = frozenset({"original_1hz", "smart_recording"})
_HR_METRIC_PRIORITY = (
    "heartrate",
    "fixed_heartrate",
    "heart_rate",
    "hr",
)
_MIN_VALID_HR_BPM = 0.0
_MAX_VALID_HR_BPM = 300.0
_INVARIANT_TOLERANCE_SEC = 1e-7


@dataclass(frozen=True, slots=True)
class HRZone:
    """One ordered, non-overlapping HR range.

    Inclusivity is explicit so an adapter can preserve the semantics of its
    source.  Touching zones are valid only when the shared boundary belongs to
    at most one of them.
    """

    name: str
    lower_bpm: float
    upper_bpm: float
    lower_inclusive: bool = True
    upper_inclusive: bool = False


def _finite_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None
    rendered = float(value)
    return rendered if math.isfinite(rendered) else None


def validate_hr_zones(zones: Sequence[HRZone]) -> tuple[HRZone, ...]:
    """Validate and freeze arbitrary ordered, non-overlapping HR zones."""

    if not isinstance(zones, Sequence) or isinstance(
        zones, (str, bytes, bytearray)
    ):
        raise ValueError("HR zones must be an ordered sequence")
    frozen = tuple(zones)
    if not frozen:
        raise ValueError("at least one HR zone is required")

    seen_names: set[str] = set()
    previous: HRZone | None = None
    for zone in frozen:
        if not isinstance(zone, HRZone):
            raise ValueError("every HR zone must be an HRZone")
        name = zone.name.strip() if isinstance(zone.name, str) else ""
        lower = _finite_number(zone.lower_bpm)
        upper = _finite_number(zone.upper_bpm)
        if not name or name in seen_names:
            raise ValueError("HR zone names must be non-empty and unique")
        if lower is None or upper is None or lower >= upper:
            raise ValueError("HR zone bounds must be finite and increasing")
        if lower < _MIN_VALID_HR_BPM or upper > _MAX_VALID_HR_BPM:
            raise ValueError("HR zone bounds are outside the supported range")
        if previous is not None:
            previous_upper = float(previous.upper_bpm)
            if lower < previous_upper:
                raise ValueError("HR zones must not overlap")
            if (
                lower == previous_upper
                and previous.upper_inclusive
                and zone.lower_inclusive
            ):
                raise ValueError("touching HR zones overlap at their boundary")
        seen_names.add(name)
        previous = zone
    return frozen


def _contains(zone: HRZone, value: float) -> bool:
    lower_ok = (
        value >= zone.lower_bpm
        if zone.lower_inclusive
        else value > zone.lower_bpm
    )
    upper_ok = (
        value <= zone.upper_bpm
        if zone.upper_inclusive
        else value < zone.upper_bpm
    )
    return lower_ok and upper_ok


def _zone_index(zones: Sequence[HRZone], value: float) -> int | None:
    for index, zone in enumerate(zones):
        if _contains(zone, value):
            return index
    return None


def _valid_hr(value: Any) -> float | None:
    rendered = _finite_number(value)
    if (
        rendered is None
        or rendered <= _MIN_VALID_HR_BPM
        or rendered > _MAX_VALID_HR_BPM
    ):
        return None
    return rendered


def _hr_metric(result: IntervalAwareResult) -> str | None:
    available = set(result.metric_names)
    return next(
        (name for name in _HR_METRIC_PRIORITY if name in available),
        None,
    )


def _interval_zone_seconds(
    left_hr: float,
    right_hr: float,
    dt_sec: float,
    zones: Sequence[HRZone],
) -> list[float]:
    """Split one linearly changing interval at every crossed HR boundary."""

    seconds = [0.0] * len(zones)
    if left_hr == right_hr:
        index = _zone_index(zones, left_hr)
        if index is not None:
            seconds[index] = dt_sec
        return seconds

    change = right_hr - left_hr
    cuts = [0.0, 1.0]
    for zone in zones:
        for boundary in (zone.lower_bpm, zone.upper_bpm):
            fraction = (boundary - left_hr) / change
            if 0.0 < fraction < 1.0:
                cuts.append(fraction)
    cuts = sorted(set(cuts))
    for start, end in zip(cuts, cuts[1:]):
        if end <= start:
            continue
        midpoint = (start + end) / 2.0
        midpoint_hr = left_hr + change * midpoint
        index = _zone_index(zones, midpoint_hr)
        if index is not None:
            seconds[index] += (end - start) * dt_sec
    return seconds


def calculate_hr_zone_time(
    interval_result: IntervalAwareResult,
    zones: Sequence[HRZone],
) -> dict[str, Any]:
    """Calculate HR-zone time directly from reliable active intervals.

    Missing or invalid HR at either endpoint makes the whole active interval
    unclassified.  No forward-fill, extrapolation, or 1 Hz materialization is
    performed.
    """

    validated_zones = validate_hr_zones(zones)
    hr_metric = _hr_metric(interval_result)
    zone_seconds = [0.0] * len(validated_zones)
    excluded_by_classification: dict[str, float] = {}
    active_interval_count = 0
    invalid_hr_interval_count = 0

    for interval in interval_result.intervals:
        if interval.classification not in _ACTIVE_CLASSIFICATIONS:
            excluded_by_classification[interval.classification] = (
                excluded_by_classification.get(interval.classification, 0.0)
                + interval.dt_sec
            )
            continue
        active_interval_count += 1
        if hr_metric is None:
            invalid_hr_interval_count += 1
            continue
        left_hr = _valid_hr(interval.left.value(hr_metric))
        right_hr = _valid_hr(interval.right.value(hr_metric))
        if left_hr is None or right_hr is None:
            invalid_hr_interval_count += 1
            continue
        contributions = _interval_zone_seconds(
            left_hr,
            right_hr,
            interval.dt_sec,
            validated_zones,
        )
        for index, seconds in enumerate(contributions):
            zone_seconds[index] += seconds

    active_duration = math.fsum(
        interval.dt_sec
        for interval in interval_result.intervals
        if interval.classification in _ACTIVE_CLASSIFICATIONS
    )
    classified = math.fsum(zone_seconds)
    if classified > active_duration + _INVARIANT_TOLERANCE_SEC:
        raise ArithmeticError("classified HR duration exceeds active duration")
    unclassified = max(active_duration - classified, 0.0)
    excluded = math.fsum(excluded_by_classification.values())
    invariant_delta = classified + unclassified - active_duration
    if abs(invariant_delta) > _INVARIANT_TOLERANCE_SEC:
        raise ArithmeticError("HR zone duration invariant failed")

    zone_rows: list[dict[str, Any]] = []
    for zone, seconds in zip(validated_zones, zone_seconds):
        zone_rows.append(
            {
                "zone": zone.name,
                "lower_bpm": float(zone.lower_bpm),
                "upper_bpm": float(zone.upper_bpm),
                "lower_inclusive": zone.lower_inclusive,
                "upper_inclusive": zone.upper_inclusive,
                "seconds": seconds,
                "minutes": seconds / 60.0,
                "percent_of_classified_hr_time": (
                    seconds / classified * 100.0 if classified else 0.0
                ),
            }
        )

    return {
        "algorithm_version": ALGORITHM_VERSION,
        "available": hr_metric is not None,
        "reason": None if hr_metric is not None else "hr_stream_unavailable",
        "hr_metric": hr_metric,
        "zones": zone_rows,
        "classified_hr_sec": classified,
        "unclassified_hr_sec": unclassified,
        "hr_coverage_percent": (
            classified / active_duration * 100.0
            if active_duration
            else 0.0
        ),
        "active_duration_sec": active_duration,
        "excluded_duration_sec": excluded,
        "excluded_duration_by_classification": dict(
            sorted(excluded_by_classification.items())
        ),
        "processed_active_interval_count": active_interval_count,
        "unclassified_endpoint_interval_count": invalid_hr_interval_count,
        "invariant_tolerance_sec": _INVARIANT_TOLERANCE_SEC,
        "invariant_delta_sec": invariant_delta,
    }
