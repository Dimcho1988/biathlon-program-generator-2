"""Privacy-minimized Intervals.icu adapter for diagnostic HR zones."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
from typing import Any

from intervals_inspector.hr_zone_engine import (
    ALGORITHM_VERSION,
    HRZone,
    calculate_hr_zone_time,
)
from intervals_inspector.stream_normalizer import IntervalAwareResult


@dataclass(frozen=True, slots=True)
class IntervalsHRZoneInput:
    zones: tuple[HRZone, ...]
    reference_seconds: tuple[float, ...] | None
    reference_reason: str | None


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    )


def _finite_positive(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None
    rendered = float(value)
    if not math.isfinite(rendered) or rendered <= 0 or rendered > 300:
        return None
    return rendered


def _finite_non_negative(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None
    rendered = float(value)
    if not math.isfinite(rendered) or rendered < 0:
        return None
    return rendered


def adapt_intervals_hr_zones(
    activity_detail: Any,
) -> tuple[IntervalsHRZoneInput | None, str | None]:
    """Adapt the observed activity-specific upper-bound array structure.

    ``icu_hr_zones`` is treated only as an ordered array containing the
    maximum BPM for each zone.  Unknown shapes are rejected explicitly.
    """

    detail = activity_detail if isinstance(activity_detail, Mapping) else {}
    raw_bounds = detail.get("icu_hr_zones")
    if raw_bounds is None:
        return None, "icu_hr_zones_missing"
    if not _is_sequence(raw_bounds) or not raw_bounds:
        return None, "icu_hr_zones_invalid_structure"

    upper_bounds: list[float] = []
    for raw_value in raw_bounds:
        upper = _finite_positive(raw_value)
        if upper is None:
            return None, "icu_hr_zones_invalid_boundaries"
        if upper_bounds and upper <= upper_bounds[-1]:
            return None, "icu_hr_zones_not_strictly_increasing"
        upper_bounds.append(upper)

    zones: list[HRZone] = []
    lower = 0.0
    for index, upper in enumerate(upper_bounds):
        zones.append(
            HRZone(
                name=f"Z{index + 1}",
                lower_bpm=lower,
                upper_bpm=upper,
                lower_inclusive=False,
                upper_inclusive=True,
            )
        )
        lower = upper

    reference: tuple[float, ...] | None = None
    reference_reason: str | None = None
    raw_reference = detail.get("icu_hr_zone_times")
    if raw_reference is None:
        reference_reason = "icu_hr_zone_times_missing"
    elif not _is_sequence(raw_reference) or len(raw_reference) != len(zones):
        reference_reason = "icu_hr_zone_times_invalid_structure"
    else:
        converted = tuple(
            _finite_non_negative(value) for value in raw_reference
        )
        if any(value is None for value in converted):
            reference_reason = "icu_hr_zone_times_invalid_values"
        else:
            reference = tuple(float(value) for value in converted if value is not None)

    return (
        IntervalsHRZoneInput(
            zones=tuple(zones),
            reference_seconds=reference,
            reference_reason=reference_reason,
        ),
        None,
    )


def analyze_intervals_hr_zones(
    interval_result: IntervalAwareResult,
    activity_detail: Any,
) -> dict[str, Any]:
    """Build a safe aggregate comparison without retaining the API payload."""

    adapted, reason = adapt_intervals_hr_zones(activity_detail)
    return build_intervals_zone_analysis(
        interval_result,
        adapted,
        unavailable_reason=reason,
    )


def build_intervals_zone_analysis(
    interval_result: IntervalAwareResult,
    adapted: IntervalsHRZoneInput | None,
    *,
    unavailable_reason: str | None = None,
) -> dict[str, Any]:
    """Calculate from an already minimized adapter result."""

    if adapted is None:
        excluded_by_classification: dict[str, float] = {}
        for interval in interval_result.intervals:
            if interval.recording_segment_id is not None:
                continue
            excluded_by_classification[interval.classification] = (
                excluded_by_classification.get(interval.classification, 0.0)
                + interval.dt_sec
            )
        return {
            "algorithm_version": ALGORITHM_VERSION,
            "available": False,
            "reason": unavailable_reason or "icu_hr_zones_unavailable",
            "zone_source": "activity_detail.icu_hr_zones",
            "zone_structure": None,
            "zones": [],
            "classified_hr_sec": 0.0,
            "unclassified_hr_sec": interval_result.active_duration_sec,
            "hr_coverage_percent": 0.0,
            "active_duration_sec": interval_result.active_duration_sec,
            "excluded_duration_sec": math.fsum(
                excluded_by_classification.values()
            ),
            "excluded_duration_by_classification": dict(
                sorted(excluded_by_classification.items())
            ),
            "intervals_reference_available": False,
            "intervals_reference_reason": "zone_configuration_unavailable",
            "invariant_delta_sec": 0.0,
        }

    analysis = calculate_hr_zone_time(interval_result, adapted.zones)
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(analysis["zones"]):
        reference = (
            adapted.reference_seconds[index]
            if adapted.reference_seconds is not None
            else None
        )
        rows.append(
            {
                **row,
                "intervals_reference_sec": reference,
                "difference_sec": (
                    row["seconds"] - reference
                    if reference is not None
                    else None
                ),
            }
        )
    return {
        **analysis,
        "zone_source": "activity_detail.icu_hr_zones",
        "zone_structure": "ordered_max_bpm_array",
        "zones": rows,
        "intervals_reference_available": (
            adapted.reference_seconds is not None
        ),
        "intervals_reference_reason": adapted.reference_reason,
    }
