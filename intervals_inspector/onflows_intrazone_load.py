"""Analytic onFlows intrazone weighting over interval-aware HR data."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
import math
from numbers import Real
from typing import Any

from intervals_inspector.onflows_zone_profile import (
    OnFlowsZone,
    OnFlowsZoneProfile,
)
from intervals_inspector.stream_normalizer import IntervalAwareResult


ALGORITHM_VERSION = "onflows-intrazone-load-interval-aware-v2-qref"
_ACTIVE_CLASSIFICATIONS = frozenset({"original_1hz", "smart_recording"})
_HR_METRIC_PRIORITY = (
    "heartrate",
    "fixed_heartrate",
    "heart_rate",
    "hr",
)
_MAX_VALID_HR_BPM = 300.0
_INVARIANT_TOLERANCE_SEC = 1e-7


def _finite_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool) or not isinstance(value, Real):
        return None
    rendered = float(value)
    return rendered if math.isfinite(rendered) else None


def _valid_hr(value: Any) -> float | None:
    rendered = _finite_number(value)
    if rendered is None or rendered <= 0 or rendered > _MAX_VALID_HR_BPM:
        return None
    return rendered


def _hr_metric(result: IntervalAwareResult) -> str | None:
    available = set(result.metric_names)
    return next(
        (name for name in _HR_METRIC_PRIORITY if name in available),
        None,
    )


def intrazone_values(hr: float, zone: OnFlowsZone) -> tuple[float, float, float]:
    """Return the legacy-compatible ``u``, ``W`` and ``k = W/W_low``."""

    width = zone.hr_high - zone.hr_low
    u = min(max((float(hr) - zone.hr_low) / width, 0.0), 1.0)
    weight = zone.weight_low + (
        zone.weight_high - zone.weight_low
    ) * (u**zone.power)
    coefficient = weight / zone.weight_low
    return u, weight, coefficient


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


def _mean_u_power(u0: float, u1: float, power: float) -> float:
    """Exact mean of ``u(t)**power`` for linearly varying ``u``."""

    if math.isclose(u0, u1, rel_tol=0.0, abs_tol=1e-15):
        return u0**power
    return (u1 ** (power + 1.0) - u0 ** (power + 1.0)) / (
        (power + 1.0) * (u1 - u0)
    )


def _segment_weighted_seconds(
    hr_start: float,
    hr_end: float,
    duration_sec: float,
    zone: OnFlowsZone,
) -> float:
    u0, _weight0, _k0 = intrazone_values(hr_start, zone)
    u1, _weight1, _k1 = intrazone_values(hr_end, zone)
    relative_weight_range = (
        zone.weight_high - zone.weight_low
    ) / zone.weight_low
    average_k = 1.0 + relative_weight_range * _mean_u_power(
        u0, u1, zone.power
    )
    return duration_sec * average_k


def calculate_onflows_intrazone_load(
    interval_result: IntervalAwareResult,
    profile: OnFlowsZoneProfile,
) -> dict[str, Any]:
    """Integrate real and weighted time without materializing 1 Hz points."""

    zones = profile.zones
    membership_lows = tuple(zone.membership_low_bpm for zone in zones)
    split_points = profile.split_points_bpm
    hr_metric = _hr_metric(interval_result)
    real_seconds = [0.0] * len(zones)
    weighted_seconds = [0.0] * len(zones)
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
        left_hr = _valid_hr(interval.left.value(hr_metric))
        right_hr = _valid_hr(interval.right.value(hr_metric))
        if left_hr is None or right_hr is None:
            invalid_hr_interval_count += 1
            continue

        change = right_hr - left_hr
        cuts = [0.0, 1.0]
        if change != 0.0:
            minimum = min(left_hr, right_hr)
            maximum = max(left_hr, right_hr)
            first = bisect_right(split_points, minimum)
            last = bisect_right(split_points, maximum)
            for boundary in split_points[first:last]:
                if boundary >= maximum:
                    continue
                fraction = (boundary - left_hr) / change
                if 0.0 < fraction < 1.0:
                    cuts.append(fraction)
                    crossed_split_point_count += 1
        cuts = sorted(set(cuts))

        for start_fraction, end_fraction in zip(cuts, cuts[1:]):
            if end_fraction <= start_fraction:
                continue
            midpoint_fraction = (start_fraction + end_fraction) / 2.0
            midpoint_hr = left_hr + change * midpoint_fraction
            zone_index = _zone_index(zones, membership_lows, midpoint_hr)
            if zone_index is None:
                continue
            segment_duration = (
                end_fraction - start_fraction
            ) * interval.dt_sec
            segment_start_hr = left_hr + change * start_fraction
            segment_end_hr = left_hr + change * end_fraction
            real_seconds[zone_index] += segment_duration
            weighted_seconds[zone_index] += _segment_weighted_seconds(
                segment_start_hr,
                segment_end_hr,
                segment_duration,
                zones[zone_index],
            )

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
    qref_seconds: list[float] = []
    for zone, real, weighted in zip(zones, real_seconds, weighted_seconds):
        maximum_weighted = real * zone.weight_high / zone.weight_low
        tolerance = max(_INVARIANT_TOLERANCE_SEC, real * 1e-12)
        if weighted < real - tolerance or weighted > maximum_weighted + tolerance:
            raise ArithmeticError("onFlows weighted-duration invariant failed")
        reference_boundary = "lower" if zone.zone == "Z5" else "upper"
        reference_weight = (
            zone.weight_low
            if reference_boundary == "lower"
            else zone.weight_high
        )
        qref = weighted * zone.weight_low / reference_weight
        if zone.zone == "Z5":
            if qref < real - tolerance:
                raise ArithmeticError("Z5 Qref must not be below real duration")
        elif qref > real + tolerance:
            raise ArithmeticError("Z1-Z4 Qref must not exceed real duration")
        qref_seconds.append(qref)
        zone_rows.append(
            {
                "zone": zone.zone,
                "hr_low": zone.hr_low,
                "hr_high": zone.hr_high,
                "membership_low_bpm": zone.membership_low_bpm,
                "membership_high_bpm": zone.membership_high_bpm,
                "weight_low": zone.weight_low,
                "weight_high": zone.weight_high,
                "power": zone.power,
                "real_seconds": real,
                "real_minutes": real / 60.0,
                "weighted_seconds": weighted,
                "weighted_minutes": weighted / 60.0,
                "qref_reference_boundary": reference_boundary,
                "qref_reference_weight": reference_weight,
                "qref_seconds": qref,
                "qref_minutes": qref / 60.0,
                "average_k": weighted / real if real else None,
                "percent_of_classified_hr_time": (
                    real / classified * 100.0 if classified else 0.0
                ),
            }
        )

    total_weighted = math.fsum(weighted_seconds)
    total_qref = math.fsum(qref_seconds)
    excluded = math.fsum(excluded_by_classification.values())
    return {
        "algorithm_version": ALGORITHM_VERSION,
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
        "hr_metric": hr_metric,
        "zones": zone_rows,
        "active_duration_sec": active_duration,
        "classified_hr_sec": classified,
        "unclassified_hr_sec": unclassified,
        "hr_coverage_percent": (
            classified / active_duration * 100.0
            if active_duration
            else 0.0
        ),
        "excluded_duration_sec": excluded,
        "excluded_duration_by_classification": dict(
            sorted(excluded_by_classification.items())
        ),
        "total_real_sec": classified,
        "total_weighted_sec": total_weighted,
        "total_qref_sec": total_qref,
        "overall_average_k": (
            total_weighted / classified if classified else None
        ),
        "processed_active_interval_count": processed_active_intervals,
        "crossed_split_point_count": crossed_split_point_count,
        "unclassified_endpoint_interval_count": invalid_hr_interval_count,
        "invariant_tolerance_sec": _INVARIANT_TOLERANCE_SEC,
        "real_duration_invariant_delta_sec": invariant_delta,
    }
