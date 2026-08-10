"""Exact interval-aware HR-zone equivalent-time integration."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
import math
from numbers import Real
from typing import Any

from intervals_inspector.effective_hr import (
    EFFECTIVE_HR_ADAPTER_VERSION,
    EFFECTIVE_HR_SOURCE,
    effective_hr,
)
from intervals_inspector.onflows_zone_profile import (
    INTRA_ZONE_EQUIVALENCE_VERSION,
    OnFlowsZone,
    OnFlowsZoneProfile,
)
from intervals_inspector.stream_normalizer import IntervalAwareResult


ALGORITHM_VERSION = "onflows-equivalent-time-interval-aware-v3-linear"
_ACTIVE_CLASSIFICATIONS = frozenset({"original_1hz", "smart_recording"})
_HR_METRIC_PRIORITY = (
    "heartrate",
    "fixed_heartrate",
    "heart_rate",
    "hr",
)
_MAX_VALID_RAW_HR_BPM = 300.0
_INVARIANT_TOLERANCE_SEC = 1e-7


def _finite_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool) or not isinstance(value, Real):
        return None
    rendered = float(value)
    return rendered if math.isfinite(rendered) else None


def _valid_raw_hr(value: Any) -> float | None:
    rendered = _finite_number(value)
    if (
        rendered is None
        or rendered <= 0.0
        or rendered > _MAX_VALID_RAW_HR_BPM
    ):
        return None
    return rendered


def _hr_metric(result: IntervalAwareResult) -> str | None:
    available = set(result.metric_names)
    return next(
        (name for name in _HR_METRIC_PRIORITY if name in available),
        None,
    )


def equivalence_coefficient(effective_hr_bpm: float, zone: OnFlowsZone) -> float:
    """Return the linear equivalent-minute value for one effective HR.

    Z1–Z4 use the upper zone boundary as 100%.  Z5 uses the lower
    boundary as 100% and is capped at its configured HRmax (``hr_high``).
    """

    slope = zone.equivalence_slope_pp_per_bpm / 100.0
    rendered_hr = float(effective_hr_bpm)
    if zone.zone == "Z5":
        capped_hr = min(rendered_hr, zone.hr_high)
        # Fractional membership can begin half a bpm below the integer Z5
        # label (for example 177.5 for a displayed 178-bpm lower boundary).
        # Z5's reference value is never below 100%, including that boundary
        # interpolation sliver.
        return max(1.0, 1.0 + slope * (capped_hr - zone.hr_low))
    return min(
        1.0,
        max(0.0, 1.0 - slope * (zone.hr_high - rendered_hr)),
    )


def _contains(zone: OnFlowsZone, hr: float) -> bool:
    lower_ok = (
        hr >= zone.membership_low_bpm
        if zone.membership_lower_inclusive
        else hr > zone.membership_low_bpm
    )
    upper_ok = (
        hr <= zone.membership_high_bpm
        if zone.membership_upper_inclusive
        else hr < zone.membership_high_bpm
    )
    return lower_ok and upper_ok


def _zone_index(
    zones: Sequence[OnFlowsZone],
    membership_lows: Sequence[float],
    hr: float,
) -> int | None:
    candidate = bisect_right(membership_lows, hr) - 1
    if candidate < 0 or not _contains(zones[candidate], hr):
        return None
    return candidate


def _segment_equivalent_seconds(
    effective_hr_start: float,
    effective_hr_end: float,
    duration_sec: float,
    zone: OnFlowsZone,
) -> float:
    # All clamp kinks are included in profile.split_points_bpm, so the
    # coefficient is linear over this segment and the trapezoid is exact.
    start_coefficient = equivalence_coefficient(effective_hr_start, zone)
    end_coefficient = equivalence_coefficient(effective_hr_end, zone)
    return duration_sec * (start_coefficient + end_coefficient) / 2.0


def _profile_hrmax(profile: OnFlowsZoneProfile) -> float | None:
    return next(
        (zone.hr_high for zone in reversed(profile.zones) if zone.zone == "Z5"),
        None,
    )


def calculate_onflows_intrazone_load(
    interval_result: IntervalAwareResult,
    profile: OnFlowsZoneProfile,
) -> dict[str, Any]:
    """Integrate real and equivalent time without materializing 1 Hz points."""

    zones = profile.zones
    membership_lows = tuple(zone.membership_low_bpm for zone in zones)
    split_points = profile.split_points_bpm
    valid_hr_max_bpm = _profile_hrmax(profile)
    hr_metric = _hr_metric(interval_result)
    real_seconds = [0.0] * len(zones)
    equivalent_seconds = [0.0] * len(zones)
    effective_hr_seconds = [0.0] * len(zones)
    raw_hr_seconds = [0.0] * len(zones)
    excluded_by_classification: dict[str, float] = {}
    processed_active_intervals = 0
    crossed_split_point_count = 0
    invalid_hr_interval_count = 0

    for interval in interval_result.intervals:
        if interval.classification not in _ACTIVE_CLASSIFICATIONS:
            excluded_by_classification[interval.classification] = (
                excluded_by_classification.get(interval.classification, 0.0)
                + interval.dt_sec
            )
            continue
        processed_active_intervals += 1
        if hr_metric is None:
            invalid_hr_interval_count += 1
            continue
        left_raw_hr = _valid_raw_hr(interval.left.value(hr_metric))
        right_raw_hr = _valid_raw_hr(interval.right.value(hr_metric))
        if left_raw_hr is None or right_raw_hr is None:
            invalid_hr_interval_count += 1
            continue

        raw_change = right_raw_hr - left_raw_hr
        cuts = [0.0, 1.0]
        if raw_change != 0.0:
            minimum = min(left_raw_hr, right_raw_hr)
            maximum = max(left_raw_hr, right_raw_hr)
            first = bisect_right(split_points, minimum)
            last = bisect_right(split_points, maximum)
            for boundary in split_points[first:last]:
                if boundary >= maximum:
                    continue
                fraction = (boundary - left_raw_hr) / raw_change
                if 0.0 < fraction < 1.0:
                    cuts.append(fraction)
                    crossed_split_point_count += 1
        cuts = sorted(set(cuts))

        for start_fraction, end_fraction in zip(cuts, cuts[1:]):
            if end_fraction <= start_fraction:
                continue
            segment_start_raw_hr = left_raw_hr + raw_change * start_fraction
            segment_end_raw_hr = left_raw_hr + raw_change * end_fraction
            segment_start_effective_hr = effective_hr(
                segment_start_raw_hr,
            )
            segment_end_effective_hr = effective_hr(
                segment_end_raw_hr,
            )
            if (
                segment_start_effective_hr is None
                or segment_end_effective_hr is None
            ):
                invalid_hr_interval_count += 1
                continue
            midpoint_effective_hr = (
                segment_start_effective_hr + segment_end_effective_hr
            ) / 2.0
            zone_index = _zone_index(
                zones,
                membership_lows,
                midpoint_effective_hr,
            )
            if zone_index is None:
                continue
            segment_duration = (
                end_fraction - start_fraction
            ) * interval.dt_sec
            real_seconds[zone_index] += segment_duration
            equivalent_seconds[zone_index] += _segment_equivalent_seconds(
                segment_start_effective_hr,
                segment_end_effective_hr,
                segment_duration,
                zones[zone_index],
            )
            effective_hr_seconds[zone_index] += segment_duration * (
                segment_start_effective_hr + segment_end_effective_hr
            ) / 2.0
            raw_hr_seconds[zone_index] += segment_duration * (
                segment_start_raw_hr + segment_end_raw_hr
            ) / 2.0

    active_duration = math.fsum(
        interval.dt_sec
        for interval in interval_result.intervals
        if interval.classification in _ACTIVE_CLASSIFICATIONS
    )
    classified = math.fsum(real_seconds)
    if classified > active_duration + _INVARIANT_TOLERANCE_SEC:
        raise ArithmeticError("classified HR duration exceeds active duration")
    unclassified = max(active_duration - classified, 0.0)
    invariant_delta = classified + unclassified - active_duration
    if abs(invariant_delta) > _INVARIANT_TOLERANCE_SEC:
        raise ArithmeticError("onFlows real-duration invariant failed")

    zone_rows: list[dict[str, Any]] = []
    for index, zone in enumerate(zones):
        real = real_seconds[index]
        equivalent = equivalent_seconds[index]
        tolerance = max(_INVARIANT_TOLERANCE_SEC, real * 1e-12)
        if zone.zone == "Z5":
            maximum_coefficient = equivalence_coefficient(zone.hr_high, zone)
            if (
                equivalent < real - tolerance
                or equivalent > real * maximum_coefficient + tolerance
            ):
                raise ArithmeticError("Z5 equivalent-time invariant failed")
        elif equivalent < -tolerance or equivalent > real + tolerance:
            raise ArithmeticError("Z1-Z4 equivalent-time invariant failed")
        mean_effective_hr = (
            effective_hr_seconds[index] / real if real else None
        )
        mean_raw_hr = raw_hr_seconds[index] / real if real else None
        average_minute_value = equivalent / real * 100.0 if real else None
        zone_rows.append(
            {
                "zone": zone.zone,
                "hr_low": zone.hr_low,
                "hr_high": zone.hr_high,
                "membership_low_bpm": zone.membership_low_bpm,
                "membership_high_bpm": zone.membership_high_bpm,
                "equivalence_slope_pp_per_bpm": (
                    zone.equivalence_slope_pp_per_bpm
                ),
                "equivalence_reference_boundary": (
                    "lower" if zone.zone == "Z5" else "upper"
                ),
                "real_seconds": real,
                "real_minutes": real / 60.0,
                "equivalent_seconds": equivalent,
                "equivalent_minutes": equivalent / 60.0,
                "mean_effective_hr_bpm": mean_effective_hr,
                "mean_raw_hr_bpm": mean_raw_hr,
                "average_minute_value_percent": average_minute_value,
                "percent_of_classified_hr_time": (
                    real / classified * 100.0 if classified else 0.0
                ),
                # Deprecated aliases. They point to the same T_eq value and do
                # not represent separate calculations.
                "weighted_seconds": equivalent,
                "weighted_minutes": equivalent / 60.0,
                "qref_seconds": equivalent,
                "qref_minutes": equivalent / 60.0,
                "average_k": equivalent / real if real else None,
            }
        )

    total_equivalent = math.fsum(equivalent_seconds)
    excluded = math.fsum(excluded_by_classification.values())
    return {
        "algorithm_version": ALGORITHM_VERSION,
        "equivalence_version": INTRA_ZONE_EQUIVALENCE_VERSION,
        "effective_hr_adapter_version": EFFECTIVE_HR_ADAPTER_VERSION,
        "effective_hr_source": EFFECTIVE_HR_SOURCE,
        "valid_hr_max_bpm": valid_hr_max_bpm,
        "available": hr_metric is not None,
        "reason": None if hr_metric is not None else "hr_stream_unavailable",
        "profile_schema_version": profile.schema_version,
        "profile_fingerprint": profile.fingerprint,
        "profile_source": profile.source,
        "profile_warnings": [
            {
                "code": warning.code,
                "message": warning.message,
                "zone": warning.zone,
            }
            for warning in profile.warnings
        ],
        "raw_hr_metric": hr_metric,
        "hr_metric": hr_metric,
        "zones": zone_rows,
        "active_duration_sec": active_duration,
        "classified_hr_sec": classified,
        "unclassified_hr_sec": unclassified,
        "hr_coverage_percent": (
            classified / active_duration * 100.0 if active_duration else 0.0
        ),
        "excluded_duration_sec": excluded,
        "excluded_duration_by_classification": dict(
            sorted(excluded_by_classification.items())
        ),
        "total_real_sec": classified,
        "total_equivalent_sec": total_equivalent,
        # Deprecated aliases for compatibility with aggregate-only consumers.
        "total_weighted_sec": total_equivalent,
        "total_qref_sec": total_equivalent,
        "overall_average_minute_value_percent": (
            total_equivalent / classified * 100.0 if classified else None
        ),
        "overall_average_k": (
            total_equivalent / classified if classified else None
        ),
        "processed_active_interval_count": processed_active_intervals,
        "crossed_split_point_count": crossed_split_point_count,
        "unclassified_endpoint_interval_count": invalid_hr_interval_count,
        "invariant_tolerance_sec": _INVARIANT_TOLERANCE_SEC,
        "real_duration_invariant_delta_sec": invariant_delta,
    }


__all__ = [
    "ALGORITHM_VERSION",
    "calculate_onflows_intrazone_load",
    "equivalence_coefficient",
]
