"""Conservative, in-memory interval-aware stream normalization.

The core normalizer consumes only relative offsets, permitted numeric stream
metrics, safe recording-stop matches, aggregate durations, and an optional
sport code. It performs no HTTP, Streamlit, filesystem, database, or logging
operations. A 1 Hz view is materialized only by an explicit function call.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, fields, is_dataclass
import math
import re
import sys
from time import perf_counter
from typing import Any

from intervals_inspector.stream_quality import (
    _classify_numeric,
    _is_location_stream,
    _iter_stream_inputs,
    _recording_stops,
    _time_intervals,
)


ALGORITHM_VERSION = "conservative-interval-aware-v1"
_SPORT_CODE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_+.-]{0,63}$")
_TIME_STREAMS = ("time", "elapsed_time", "offset_sec", "offset")
_SPEED_STREAMS = (
    "velocity_smooth",
    "fixed_velocity_smooth",
    "speed",
    "velocity",
)
_PERMITTED_METRICS = {
    "air_power",
    "altitude",
    "cadence",
    "distance",
    "fixed_altitude",
    "fixed_heartrate",
    "fixed_velocity_smooth",
    "fixed_watts",
    "form_power",
    "grade_adjusted_speed",
    "gradient",
    "ground_contact_time",
    "heartrate",
    "heart_rate",
    "hr",
    "leg_spring_stiffness",
    "power",
    "respiration",
    "run_cadence",
    "running_cadence",
    "speed",
    "stance_time_balance",
    "stride_length",
    "temp",
    "temperature",
    "velocity",
    "velocity_smooth",
    "vertical_oscillation",
    "vertical_ratio",
    "watts",
    "watts_alt",
    "watts_alt_acc",
}
_NON_NEGATIVE_METRICS = {
    "air_power",
    "cadence",
    "distance",
    "fixed_heartrate",
    "fixed_velocity_smooth",
    "fixed_watts",
    "form_power",
    "grade_adjusted_speed",
    "ground_contact_time",
    "heartrate",
    "heart_rate",
    "hr",
    "leg_spring_stiffness",
    "power",
    "respiration",
    "run_cadence",
    "running_cadence",
    "speed",
    "stride_length",
    "velocity",
    "velocity_smooth",
    "vertical_oscillation",
    "vertical_ratio",
    "watts",
    "watts_alt",
    "watts_alt_acc",
}
_ACTIVE_CLASSIFICATIONS = {"original_1hz", "smart_recording"}


@dataclass(frozen=True, slots=True)
class NormalizerConfig:
    """Centralized conservative thresholds for the first algorithm version."""

    exact_1hz_tolerance_sec: float = 1e-9
    short_smart_recording_max_sec: float = 5.0
    extended_smart_recording_max_sec: float = 10.0
    uncertain_gap_max_sec: float = 30.0
    stop_boundary_precision_digits: int = 6
    reconciliation_tolerance_sec: float = 2.0
    reconciliation_tolerance_ratio: float = 0.001


DEFAULT_CONFIG = NormalizerConfig()


@dataclass(frozen=True, slots=True)
class NormalizerInput:
    """Privacy-minimized, transient input accepted by the core normalizer."""

    offsets: Sequence[Any]
    metrics: Mapping[str, Sequence[Any]]
    recording_stop_bounds: frozenset[tuple[float, float]] = frozenset()
    recording_stop_marker_count: int = 0
    unmatched_recording_stop_marker_count: int = 0
    elapsed_time_sec: float | None = None
    icu_recording_time_sec: float | None = None
    moving_time_sec: float | None = None
    sport_type: str | None = None
    excluded_location_stream_count: int = 0


@dataclass(frozen=True, slots=True)
class NormalizedPoint:
    """One validated original endpoint or explicitly generated preview point."""

    offset_sec: float
    values: tuple[tuple[str, float | None], ...]
    source: str
    quality_flags: tuple[str, ...] = ()
    invalid_metrics: tuple[str, ...] = ()
    ordinal: int = -1
    source_index: int = -1

    def value(self, metric_name: str) -> float | None:
        folded = metric_name.casefold()
        for name, value in self.values:
            if name == folded:
                return value
        return None


@dataclass(frozen=True, slots=True)
class TimeInterval:
    """Lightweight interval referencing its validated endpoint objects."""

    start_offset_sec: float
    end_offset_sec: float
    dt_sec: float
    classification: str
    left: NormalizedPoint
    right: NormalizedPoint
    interpolation_allowed: bool
    interpolation_source: str | None
    confidence: str
    quality_flags: tuple[str, ...]
    recording_segment_id: int | None


@dataclass(frozen=True, slots=True)
class NormalizerWarning:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class IntervalAwareResult:
    """Internal interval result; it is never serialized or persisted."""

    algorithm_version: str
    config: NormalizerConfig
    metric_names: tuple[str, ...]
    points: tuple[NormalizedPoint, ...]
    intervals: tuple[TimeInterval, ...]
    input_point_count: int
    valid_offset_point_count: int
    unique_valid_point_count: int
    duplicate_offset_count: int
    invalid_offset_count: int
    invalid_values_by_metric: tuple[tuple[str, int], ...]
    input_was_sorted: bool
    sorted_fallback_used: bool
    fast_path_used: bool
    recording_segment_count: int
    active_duration_sec: float
    stream_duration_sec: float | None
    recording_stop_marker_count: int
    unmatched_recording_stop_marker_count: int
    elapsed_time_sec: float | None
    icu_recording_time_sec: float | None
    moving_time_sec: float | None
    excluded_location_stream_count: int
    warnings: tuple[NormalizerWarning, ...]
    processing_time_ms: float = field(compare=False)


@dataclass(frozen=True, slots=True)
class OneHzResult:
    """Explicit temporary 1 Hz view; points never enter a safe summary."""

    points: tuple[NormalizedPoint, ...]
    segment_slices: tuple[tuple[int, int], ...]
    active_duration_sec: float
    reused_interval_points: bool
    created_new_points: bool
    processing_time_ms: float = field(compare=False)


def _safe_duration(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) and result >= 0 else None


def _safe_sport_type(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    rendered = value.strip()
    return rendered if _SPORT_CODE_RE.fullmatch(rendered) else None


def _bound_key(
    start: float,
    end: float,
    precision_digits: int,
) -> tuple[float, float]:
    return round(float(start), precision_digits), round(
        float(end), precision_digits
    )


def _permitted_metric_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    folded = value.strip().casefold()
    return folded if folded in _PERMITTED_METRICS else None


def build_normalizer_input(
    activity_detail: Any,
    streams: Any,
    *,
    config: NormalizerConfig = DEFAULT_CONFIG,
) -> NormalizerInput:
    """Strip a transient API response to the core normalizer's safe contract.

    Location streams are discarded on encounter. Only permitted numeric
    metric containers and the selected relative-time container remain
    referenced. Recording-stop markers are reduced to matched relative gap
    boundaries plus aggregate counts; marker values never enter the result.
    """

    detail = activity_detail if isinstance(activity_detail, Mapping) else {}
    time_candidates: dict[str, Sequence[Any]] = {}
    metrics: dict[str, Sequence[Any]] = {}
    seen: set[str] = set()
    excluded_location_count = 0

    for raw_name, points in _iter_stream_inputs(streams):
        folded = raw_name.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        if _is_location_stream(folded):
            excluded_location_count += 1
            del points
            continue
        if folded in _TIME_STREAMS:
            time_candidates[folded] = points
            continue
        metric_name = _permitted_metric_name(folded)
        if metric_name is not None:
            metrics[metric_name] = points

    time_points: Sequence[Any] = ()
    for alias in _TIME_STREAMS:
        if alias in time_candidates:
            time_points = time_candidates[alias]
            break

    _time_row, _numeric, aligned_offsets = _classify_numeric(time_points)
    raw_intervals = _time_intervals(aligned_offsets)
    valid_offsets = [item for item in aligned_offsets if item is not None]
    stream_duration = (
        max(valid_offsets) - min(valid_offsets)
        if len(valid_offsets) >= 2
        else None
    )
    stop_summary, matched_stop_indexes = _recording_stops(
        detail,
        raw_intervals,
        stream_duration,
    )
    stop_bounds = frozenset(
        _bound_key(
            float(interval["start_offset_sec"]),
            float(interval["end_offset_sec"]),
            config.stop_boundary_precision_digits,
        )
        for interval in raw_intervals
        if int(interval["left_index"]) in matched_stop_indexes
    )

    sport_type = _safe_sport_type(
        detail.get("type") or detail.get("sub_type")
    )
    stop_count = int(stop_summary.get("count", 0))
    matched_gap_count = int(stop_summary.get("matched_gap_count", 0))
    return NormalizerInput(
        offsets=time_points,
        metrics=metrics,
        recording_stop_bounds=stop_bounds,
        recording_stop_marker_count=stop_count,
        unmatched_recording_stop_marker_count=max(
            stop_count - matched_gap_count, 0
        ),
        elapsed_time_sec=_safe_duration(detail.get("elapsed_time")),
        icu_recording_time_sec=_safe_duration(
            detail.get("icu_recording_time")
        ),
        moving_time_sec=_safe_duration(detail.get("moving_time")),
        sport_type=sport_type,
        excluded_location_stream_count=excluded_location_count,
    )


def _finite_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None
    rendered = float(value)
    return rendered if math.isfinite(rendered) else None


def _metric_value(
    metric_name: str,
    raw_value: Any,
) -> tuple[float | None, bool]:
    if raw_value is None:
        return None, False
    numeric = _finite_number(raw_value)
    if numeric is None:
        return None, True
    if metric_name in _NON_NEGATIVE_METRICS and numeric < 0:
        return None, True
    return numeric, False


def _raw_at(values: Sequence[Any], index: int) -> Any:
    try:
        return values[index]
    except (IndexError, KeyError, TypeError):
        return None


def _prepare_points(
    normalizer_input: NormalizerInput,
) -> tuple[
    tuple[NormalizedPoint, ...],
    tuple[str, ...],
    int,
    int,
    bool,
    bool,
    tuple[tuple[str, int], ...],
]:
    metric_sources: dict[str, Sequence[Any]] = {}
    for raw_name, values in normalizer_input.metrics.items():
        name = _permitted_metric_name(raw_name)
        if name is not None and name not in metric_sources:
            metric_sources[name] = values
    metric_names = tuple(sorted(metric_sources))

    valid_rows: list[tuple[float, int]] = []
    invalid_offset_count = 0
    input_was_sorted = True
    previous_offset: float | None = None
    for index, raw_offset in enumerate(normalizer_input.offsets):
        offset = _finite_number(raw_offset)
        if offset is None or offset < 0:
            invalid_offset_count += 1
            continue
        if previous_offset is not None and offset < previous_offset:
            input_was_sorted = False
        previous_offset = offset
        valid_rows.append((offset, index))

    sorted_fallback_used = not input_was_sorted
    if sorted_fallback_used:
        valid_rows.sort(key=lambda item: (item[0], item[1]))

    groups: list[list[tuple[float, int]]] = []
    for row in valid_rows:
        if not groups or row[0] != groups[-1][0][0]:
            groups.append([row])
        else:
            groups[-1].append(row)
    duplicate_offset_count = sum(len(group) - 1 for group in groups)

    invalid_by_metric = {name: 0 for name in metric_names}
    points: list[NormalizedPoint] = []
    for ordinal, group in enumerate(groups):
        point_values: list[tuple[str, float | None]] = []
        point_invalid_metrics: set[str] = set()
        for metric_name in metric_names:
            chosen: float | None = None
            for _offset, source_index in group:
                value, invalid = _metric_value(
                    metric_name,
                    _raw_at(metric_sources[metric_name], source_index),
                )
                if invalid:
                    invalid_by_metric[metric_name] += 1
                    point_invalid_metrics.add(metric_name)
                elif value is not None:
                    # Deterministic duplicate rule: the last valid source value
                    # in stable original input order wins for this metric.
                    chosen = value
            point_values.append((metric_name, chosen))

        flags: list[str] = []
        if len(group) > 1:
            flags.append("duplicate_offset")
        if point_invalid_metrics:
            flags.append("invalid_source_value")
        if any(value is None for _name, value in point_values):
            flags.append("missing_value")
        points.append(
            NormalizedPoint(
                offset_sec=group[0][0],
                values=tuple(point_values),
                source="original",
                quality_flags=tuple(flags),
                invalid_metrics=tuple(sorted(point_invalid_metrics)),
                ordinal=ordinal,
                source_index=group[-1][1],
            )
        )

    return (
        tuple(points),
        metric_names,
        invalid_offset_count,
        duplicate_offset_count,
        input_was_sorted,
        sorted_fallback_used,
        tuple(sorted(invalid_by_metric.items())),
    )


def _speed_metric(metric_names: Sequence[str]) -> str | None:
    available = set(metric_names)
    return next((name for name in _SPEED_STREAMS if name in available), None)


def _is_exact_1hz(dt_sec: float, config: NormalizerConfig) -> bool:
    return math.isclose(
        dt_sec,
        1.0,
        rel_tol=0.0,
        abs_tol=config.exact_1hz_tolerance_sec,
    )


def _classify_interval(
    left: NormalizedPoint,
    right: NormalizedPoint,
    *,
    matched_stop: bool,
    speed_metric: str | None,
    pause_budget_sec: float,
    config: NormalizerConfig,
) -> tuple[str, bool, str | None, str, tuple[str, ...], bool]:
    dt_sec = right.offset_sec - left.offset_sec
    flags: list[str] = []
    if matched_stop:
        return (
            "recording_stop",
            False,
            None,
            "high",
            ("recording_stop_match",),
            False,
        )
    if _is_exact_1hz(dt_sec, config):
        return "original_1hz", False, None, "high", (), False
    if 1.0 < dt_sec <= config.short_smart_recording_max_sec:
        return (
            "smart_recording",
            True,
            "interpolated_short",
            "high",
            (),
            False,
        )

    left_speed = left.value(speed_metric) if speed_metric else None
    right_speed = right.value(speed_metric) if speed_metric else None
    speed_positive_both = (
        left_speed is not None
        and right_speed is not None
        and left_speed > 0
        and right_speed > 0
    )
    speed_zero_near_gap = left_speed == 0 or right_speed == 0
    if (
        config.short_smart_recording_max_sec
        < dt_sec
        <= config.extended_smart_recording_max_sec
    ):
        if speed_positive_both:
            return (
                "smart_recording",
                True,
                "interpolated_extended",
                "medium",
                (),
                False,
            )
        flags.append("positive_speed_endpoints_required")
        if speed_metric is None:
            flags.append("speed_unavailable")
        elif left_speed is None or right_speed is None:
            flags.append("speed_endpoint_missing_or_invalid")
        elif speed_zero_near_gap:
            flags.append("zero_speed_endpoint")
        return "uncertain_gap", False, None, "low", tuple(flags), False

    if (
        config.extended_smart_recording_max_sec
        < dt_sec
        <= config.uncertain_gap_max_sec
    ):
        return "uncertain_gap", False, None, "low", (), False

    if dt_sec > config.uncertain_gap_max_sec:
        if speed_zero_near_gap:
            return (
                "probable_pause",
                False,
                None,
                "medium",
                ("zero_speed_endpoint",),
                True,
            )
        tolerance = max(
            config.reconciliation_tolerance_sec,
            dt_sec * config.reconciliation_tolerance_ratio,
        )
        if pause_budget_sec + tolerance >= dt_sec:
            return (
                "probable_pause",
                False,
                None,
                "low",
                ("duration_reconciliation_pause_evidence",),
                True,
            )
        return (
            "technical_or_unexplained_gap",
            False,
            None,
            "low",
            (),
            False,
        )

    return (
        "uncertain_gap",
        False,
        None,
        "low",
        ("unsupported_subsecond_interval",),
        False,
    )


def _reconciliation_warnings(
    *,
    active_duration_sec: float,
    stream_duration_sec: float | None,
    elapsed_time_sec: float | None,
    icu_recording_time_sec: float | None,
    config: NormalizerConfig,
) -> list[NormalizerWarning]:
    warnings: list[NormalizerWarning] = []
    scale = max(
        active_duration_sec,
        stream_duration_sec or 0.0,
        elapsed_time_sec or 0.0,
        icu_recording_time_sec or 0.0,
        1.0,
    )
    tolerance = max(
        config.reconciliation_tolerance_sec,
        scale * config.reconciliation_tolerance_ratio,
    )
    if (
        icu_recording_time_sec is not None
        and abs(active_duration_sec - icu_recording_time_sec) > tolerance
    ):
        warnings.append(
            NormalizerWarning(
                "active_recording_time_mismatch",
                "Active interval duration и icu_recording_time не се "
                "съгласуват в диагностичната толерантност.",
            )
        )
    if (
        elapsed_time_sec is not None
        and stream_duration_sec is not None
        and abs(elapsed_time_sec - stream_duration_sec) > tolerance
    ):
        warnings.append(
            NormalizerWarning(
                "elapsed_stream_time_mismatch",
                "Elapsed time и относителната stream duration не се "
                "съгласуват в диагностичната толерантност.",
            )
        )
    return warnings


def normalize_stream_intervals(
    normalizer_input: NormalizerInput,
    *,
    config: NormalizerConfig = DEFAULT_CONFIG,
) -> IntervalAwareResult:
    """Build the lightweight interval-aware result without creating 1 Hz points."""

    started = perf_counter()
    (
        points,
        metric_names,
        invalid_offset_count,
        duplicate_offset_count,
        input_was_sorted,
        sorted_fallback_used,
        invalid_values_by_metric,
    ) = _prepare_points(normalizer_input)

    stop_bounds: set[tuple[float, float]] = set()
    for raw_start, raw_end in normalizer_input.recording_stop_bounds:
        start = _finite_number(raw_start)
        end = _finite_number(raw_end)
        if start is None or end is None or end <= start:
            continue
        stop_bounds.add(
            _bound_key(start, end, config.stop_boundary_precision_digits)
        )
    matched_stop_flags = tuple(
        _bound_key(
            points[index].offset_sec,
            points[index + 1].offset_sec,
            config.stop_boundary_precision_digits,
        )
        in stop_bounds
        for index in range(len(points) - 1)
    )
    matched_stop_duration = sum(
        points[index + 1].offset_sec - points[index].offset_sec
        for index, matched in enumerate(matched_stop_flags)
        if matched
    )
    elapsed_minus_recording = (
        normalizer_input.elapsed_time_sec
        - normalizer_input.icu_recording_time_sec
        if normalizer_input.elapsed_time_sec is not None
        and normalizer_input.icu_recording_time_sec is not None
        else 0.0
    )
    pause_budget = max(elapsed_minus_recording - matched_stop_duration, 0.0)
    speed_metric = _speed_metric(metric_names)

    intervals: list[TimeInterval] = []
    segment_id = -1
    inside_active_segment = False
    for index, matched_stop in enumerate(matched_stop_flags):
        left = points[index]
        right = points[index + 1]
        (
            classification,
            interpolation_allowed,
            interpolation_source,
            confidence,
            flags,
            consumes_pause_budget,
        ) = _classify_interval(
            left,
            right,
            matched_stop=matched_stop,
            speed_metric=speed_metric,
            pause_budget_sec=pause_budget,
            config=config,
        )
        if consumes_pause_budget:
            pause_budget = max(
                pause_budget - (right.offset_sec - left.offset_sec),
                0.0,
            )
        if classification in _ACTIVE_CLASSIFICATIONS:
            if not inside_active_segment:
                segment_id += 1
                inside_active_segment = True
            interval_segment_id: int | None = segment_id
        else:
            inside_active_segment = False
            interval_segment_id = None
        intervals.append(
            TimeInterval(
                start_offset_sec=left.offset_sec,
                end_offset_sec=right.offset_sec,
                dt_sec=right.offset_sec - left.offset_sec,
                classification=classification,
                left=left,
                right=right,
                interpolation_allowed=interpolation_allowed,
                interpolation_source=interpolation_source,
                confidence=confidence,
                quality_flags=flags,
                recording_segment_id=interval_segment_id,
            )
        )

    interval_tuple = tuple(intervals)
    active_duration = sum(
        item.dt_sec
        for item in interval_tuple
        if item.classification in _ACTIVE_CLASSIFICATIONS
    )
    stream_duration = (
        points[-1].offset_sec - points[0].offset_sec
        if len(points) >= 2
        else 0.0 if points else None
    )
    segment_count = segment_id + 1
    fast_path_used = (
        bool(interval_tuple)
        and invalid_offset_count == 0
        and duplicate_offset_count == 0
        and input_was_sorted
        and not stop_bounds
        and normalizer_input.recording_stop_marker_count == 0
        and all(
            item.classification == "original_1hz"
            for item in interval_tuple
        )
    )

    warnings: list[NormalizerWarning] = []
    if invalid_offset_count:
        warnings.append(
            NormalizerWarning(
                "invalid_offsets_excluded",
                "Невалидни или отрицателни offsets са изключени.",
            )
        )
    if duplicate_offset_count:
        warnings.append(
            NormalizerWarning(
                "duplicate_offsets_resolved",
                "Duplicate offsets са обединени с правилото last valid "
                "source value per metric wins.",
            )
        )
    if sorted_fallback_used:
        warnings.append(
            NormalizerWarning(
                "input_sorted_by_offset",
                "Входът не беше подреден и е приложено еднократно stable "
                "sorting по относителен offset.",
            )
        )
    if normalizer_input.unmatched_recording_stop_marker_count:
        warnings.append(
            NormalizerWarning(
                "recording_stop_markers_unmatched",
                "Част от recording-stop маркерите не могат да се "
                "съпоставят надеждно с относителни stream gaps.",
            )
        )
    if any(
        item.classification == "technical_or_unexplained_gap"
        for item in interval_tuple
    ):
        warnings.append(
            NormalizerWarning(
                "technical_or_unexplained_gaps_present",
                "Има големи gaps без достатъчно pause или stop evidence.",
            )
        )
    warnings.extend(
        _reconciliation_warnings(
            active_duration_sec=active_duration,
            stream_duration_sec=stream_duration,
            elapsed_time_sec=normalizer_input.elapsed_time_sec,
            icu_recording_time_sec=normalizer_input.icu_recording_time_sec,
            config=config,
        )
    )

    return IntervalAwareResult(
        algorithm_version=ALGORITHM_VERSION,
        config=config,
        metric_names=metric_names,
        points=points,
        intervals=interval_tuple,
        input_point_count=len(normalizer_input.offsets),
        valid_offset_point_count=len(normalizer_input.offsets)
        - invalid_offset_count,
        unique_valid_point_count=len(points),
        duplicate_offset_count=duplicate_offset_count,
        invalid_offset_count=invalid_offset_count,
        invalid_values_by_metric=invalid_values_by_metric,
        input_was_sorted=input_was_sorted,
        sorted_fallback_used=sorted_fallback_used,
        fast_path_used=fast_path_used,
        recording_segment_count=segment_count,
        active_duration_sec=active_duration,
        stream_duration_sec=stream_duration,
        recording_stop_marker_count=max(
            int(normalizer_input.recording_stop_marker_count), 0
        ),
        unmatched_recording_stop_marker_count=max(
            int(normalizer_input.unmatched_recording_stop_marker_count), 0
        ),
        elapsed_time_sec=normalizer_input.elapsed_time_sec,
        icu_recording_time_sec=normalizer_input.icu_recording_time_sec,
        moving_time_sec=normalizer_input.moving_time_sec,
        excluded_location_stream_count=max(
            int(normalizer_input.excluded_location_stream_count), 0
        ),
        warnings=tuple(warnings),
        processing_time_ms=(perf_counter() - started) * 1000.0,
    )


def _interior_second_count(start: float, end: float) -> int:
    return max(math.ceil(end) - math.floor(start) - 1, 0)


def _interior_second_offsets(start: float, end: float):
    first = math.floor(start) + 1
    last = math.ceil(end) - 1
    for offset in range(first, last + 1):
        if start < offset < end:
            yield float(offset)


def _interpolated_point(
    interval: TimeInterval,
    offset_sec: float,
) -> NormalizedPoint:
    fraction = (offset_sec - interval.start_offset_sec) / interval.dt_sec
    left_values = dict(interval.left.values)
    right_values = dict(interval.right.values)
    values: list[tuple[str, float | None]] = []
    for metric_name in left_values:
        left = left_values[metric_name]
        right = right_values.get(metric_name)
        value = (
            left + (right - left) * fraction
            if left is not None and right is not None
            else None
        )
        values.append((metric_name, value))
    flags: list[str] = []
    if any(value is None for _name, value in values):
        flags.append("missing_value")
    invalid_metrics = tuple(
        sorted(
            set(interval.left.invalid_metrics)
            | set(interval.right.invalid_metrics)
        )
    )
    if invalid_metrics:
        flags.append("invalid_source_value")
    return NormalizedPoint(
        offset_sec=offset_sec,
        values=tuple(values),
        source=interval.interpolation_source or "interpolated_short",
        quality_flags=tuple(flags),
        invalid_metrics=invalid_metrics,
    )


def materialize_1hz(result: IntervalAwareResult) -> OneHzResult:
    """Explicitly create a temporary 1 Hz view for allowed active segments."""

    started = perf_counter()
    if result.fast_path_used:
        segment_slices = ((0, len(result.points)),) if result.points else ()
        return OneHzResult(
            points=result.points,
            segment_slices=segment_slices,
            active_duration_sec=result.active_duration_sec,
            reused_interval_points=True,
            created_new_points=False,
            processing_time_ms=(perf_counter() - started) * 1000.0,
        )

    output: list[NormalizedPoint] = []
    segment_slices: list[tuple[int, int]] = []
    current_segment_id: int | None = None
    segment_start = 0
    for interval in result.intervals:
        if interval.classification not in _ACTIVE_CLASSIFICATIONS:
            continue
        if interval.recording_segment_id != current_segment_id:
            if current_segment_id is not None:
                segment_slices.append((segment_start, len(output)))
            current_segment_id = interval.recording_segment_id
            segment_start = len(output)
            output.append(interval.left)
        for offset in _interior_second_offsets(
            interval.start_offset_sec,
            interval.end_offset_sec,
        ):
            output.append(_interpolated_point(interval, offset))
        if not output or output[-1].offset_sec != interval.right.offset_sec:
            output.append(interval.right)
    if current_segment_id is not None:
        segment_slices.append((segment_start, len(output)))

    return OneHzResult(
        points=tuple(output),
        segment_slices=tuple(segment_slices),
        active_duration_sec=result.active_duration_sec,
        reused_interval_points=False,
        created_new_points=any(
            point.source != "original" for point in output
        ),
        processing_time_ms=(perf_counter() - started) * 1000.0,
    )


def _approximate_size(value: Any, seen: set[int] | None = None) -> int:
    if seen is None:
        seen = set()
    identity = id(value)
    if identity in seen:
        return 0
    seen.add(identity)
    size = sys.getsizeof(value)
    if is_dataclass(value):
        return size + sum(
            _approximate_size(getattr(value, item.name), seen)
            for item in fields(value)
        )
    if isinstance(value, Mapping):
        return size + sum(
            _approximate_size(key, seen) + _approximate_size(item, seen)
            for key, item in value.items()
        )
    if isinstance(value, (tuple, list, set, frozenset)):
        return size + sum(_approximate_size(item, seen) for item in value)
    return size


def approximate_result_size_bytes(value: Any) -> int:
    """Return a technical, approximate deep in-memory size for diagnostics."""

    return _approximate_size(value)


def _classification_summary(
    intervals: Sequence[TimeInterval],
) -> dict[str, dict[str, float | int]]:
    names = (
        "original_1hz",
        "smart_recording",
        "uncertain_gap",
        "recording_stop",
        "probable_pause",
        "technical_or_unexplained_gap",
    )
    return {
        name: {
            "interval_count": sum(
                item.classification == name for item in intervals
            ),
            "duration_sec": sum(
                item.dt_sec
                for item in intervals
                if item.classification == name
            ),
        }
        for name in names
    }


def _active_point_ordinals(
    intervals: Sequence[TimeInterval],
) -> set[int]:
    ordinals: set[int] = set()
    for interval in intervals:
        if interval.classification in _ACTIVE_CLASSIFICATIONS:
            ordinals.add(interval.left.ordinal)
            ordinals.add(interval.right.ordinal)
    return ordinals


def _potential_interpolation_counts(
    intervals: Sequence[TimeInterval],
) -> tuple[int, int]:
    short = 0
    extended = 0
    for interval in intervals:
        if not interval.interpolation_allowed or not interval.interpolation_source:
            continue
        count = _interior_second_count(
            interval.start_offset_sec,
            interval.end_offset_sec,
        )
        if interval.interpolation_source == "interpolated_short":
            short += count
        else:
            extended += count
    return short, extended


def _missing_point_estimate(result: IntervalAwareResult) -> int:
    active_ordinals = _active_point_ordinals(result.intervals)
    missing_original = sum(
        point.ordinal in active_ordinals and "missing_value" in point.quality_flags
        for point in result.points
    )
    missing_generated = 0
    for interval in result.intervals:
        if not interval.interpolation_allowed or not interval.interpolation_source:
            continue
        if any(
            left_value is None or interval.right.value(name) is None
            for name, left_value in interval.left.values
        ):
            missing_generated += _interior_second_count(
                interval.start_offset_sec,
                interval.end_offset_sec,
            )
    return missing_original + missing_generated


def _reconciliation_summary(result: IntervalAwareResult) -> dict[str, Any]:
    recording_minus_active = (
        result.icu_recording_time_sec - result.active_duration_sec
        if result.icu_recording_time_sec is not None
        else None
    )
    recording_minus_moving = (
        result.icu_recording_time_sec - result.moving_time_sec
        if result.icu_recording_time_sec is not None
        and result.moving_time_sec is not None
        else None
    )
    elapsed_minus_stream = (
        result.elapsed_time_sec - result.stream_duration_sec
        if result.elapsed_time_sec is not None
        and result.stream_duration_sec is not None
        else None
    )
    return {
        "elapsed_time_sec": result.elapsed_time_sec,
        "stream_duration_sec": result.stream_duration_sec,
        "icu_recording_time_sec": result.icu_recording_time_sec,
        "moving_time_sec": result.moving_time_sec,
        "active_duration_sec": result.active_duration_sec,
        "icu_recording_minus_active_sec": recording_minus_active,
        "icu_recording_minus_moving_sec": recording_minus_moving,
        "elapsed_minus_stream_sec": elapsed_minus_stream,
    }


def build_normalizer_summary(
    result: IntervalAwareResult,
    materialized: OneHzResult | None = None,
) -> dict[str, Any]:
    """Return aggregate-only diagnostics for interval and optional 1 Hz modes."""

    classifications = _classification_summary(result.intervals)
    active_ordinals = _active_point_ordinals(result.intervals)
    short_count, extended_count = _potential_interpolation_counts(
        result.intervals
    )
    original_count = len(active_ordinals)
    potential_count = original_count + short_count + extended_count
    interpolated_count = short_count + extended_count
    original_percent = (
        round(original_count / potential_count * 100.0, 2)
        if potential_count
        else 0.0
    )
    interpolation_percent = (
        round(interpolated_count / potential_count * 100.0, 2)
        if potential_count
        else 0.0
    )
    invalid_values = {
        name: count
        for name, count in result.invalid_values_by_metric
        if count
    }
    interval_result_size = approximate_result_size_bytes(result)
    materialized_result_size = (
        approximate_result_size_bytes(materialized) if materialized else 0
    )
    materialized_additional_size = (
        max(
            approximate_result_size_bytes((result, materialized))
            - interval_result_size,
            0,
        )
        if materialized
        else 0
    )
    materialization_summary: dict[str, Any] = {
        "requested": materialized is not None,
        "created": materialized is not None,
        "point_count": len(materialized.points) if materialized else 0,
        "segment_count": (
            len(materialized.segment_slices) if materialized else 0
        ),
        "active_duration_sec": (
            materialized.active_duration_sec if materialized else None
        ),
        "reused_interval_points": (
            materialized.reused_interval_points if materialized else False
        ),
        "created_new_points": (
            materialized.created_new_points if materialized else False
        ),
        "processing_time_ms": (
            materialized.processing_time_ms if materialized else None
        ),
        "approximate_result_size_bytes": materialized_result_size,
        "approximate_additional_size_bytes": materialized_additional_size,
    }
    return {
        "algorithm_version": result.algorithm_version,
        "config": asdict(result.config),
        "path": "fast_path" if result.fast_path_used else "interval_aware",
        "fast_path_used": result.fast_path_used,
        "input_point_count": result.input_point_count,
        "valid_offset_point_count": result.valid_offset_point_count,
        "unique_valid_point_count": result.unique_valid_point_count,
        "interval_count": len(result.intervals),
        "potential_1hz_point_count": potential_count,
        "normalized_second_count_estimate": potential_count,
        "original_active_point_count": original_count,
        "original_second_count": original_count,
        "interpolated_short_point_count": short_count,
        "interpolated_short_second_count": short_count,
        "interpolated_extended_point_count": extended_count,
        "interpolated_extended_second_count": extended_count,
        "points_with_missing_metrics_estimate": _missing_point_estimate(result),
        "recording_segment_count": result.recording_segment_count,
        "active_duration_sec": result.active_duration_sec,
        "recording_stop_marker_count": result.recording_stop_marker_count,
        "unmatched_recording_stop_marker_count": (
            result.unmatched_recording_stop_marker_count
        ),
        "duplicate_offset_count": result.duplicate_offset_count,
        "invalid_offset_count": result.invalid_offset_count,
        "invalid_values_by_metric": invalid_values,
        "original_point_percent": original_percent,
        "interpolated_point_percent": interpolation_percent,
        "classifications": classifications,
        "reconciliation": _reconciliation_summary(result),
        "excluded_location_stream_count": (
            result.excluded_location_stream_count
        ),
        "warnings": [asdict(warning) for warning in result.warnings],
        "processing_time_ms": result.processing_time_ms,
        "approximate_interval_result_size_bytes": interval_result_size,
        "materialize_1hz": materialization_summary,
    }
