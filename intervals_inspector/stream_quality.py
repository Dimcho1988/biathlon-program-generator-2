"""In-memory, deidentified diagnostics for one activity's streams.

This module deliberately has no Streamlit, HTTP, OAuth, file-system, logging,
or persistence dependency.  It accepts transient API payloads and returns only
aggregate counts and statistics.  Location coordinates and absolute timestamp
values are never copied into the result.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import json
import math
import re
from statistics import median, pstdev
from typing import Any


_STREAM_CODE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_ISO_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?"
    r"(?:[Zz]|[+-]\d{2}:?\d{2})?$"
)
_LOCATION_STREAMS = {
    "gps",
    "lat",
    "latitude",
    "latlng",
    "lng",
    "lon",
    "longitude",
}
_ABSOLUTE_TIME_STREAMS = {
    "date_time",
    "datetime",
    "timestamp",
    "timestamps",
}
_TIME_STREAMS = ("time", "elapsed_time", "offset_sec", "offset")
_HR_STREAMS = ("heartrate", "fixed_heartrate", "heart_rate", "hr")
_SPEED_STREAMS = (
    "velocity_smooth",
    "speed",
    "fixed_velocity_smooth",
    "velocity",
)
_CADENCE_STREAMS = ("cadence", "run_cadence", "running_cadence")
_ALTITUDE_STREAMS = ("altitude", "fixed_altitude", "elevation")
_POWER_STREAMS = (
    "watts",
    "power",
    "fixed_watts",
    "raw_watts",
    "watts_alt",
    "watts_alt_acc",
)
_SENSITIVE_STREAM_TOKENS = (
    "authorization",
    "credential",
    "oauth",
    "password",
    "secret",
    "token",
)
_HR_PLAUSIBLE_MIN = 20.0
_HR_PLAUSIBLE_MAX = 260.0
_FIT_EPOCH_UNIX_SEC = 631065600.0
_GAP_THRESHOLD_SEC = 1.5
_LARGE_GAP_THRESHOLD_SEC = 30.0
_DT_BUCKETS: tuple[tuple[str, str], ...] = (
    ("dt_eq_1s", "dt = 1 sec"),
    ("dt_eq_2s", "dt = 2 sec"),
    ("dt_eq_3s", "dt = 3 sec"),
    ("dt_eq_4s", "dt = 4 sec"),
    ("dt_eq_5s", "dt = 5 sec"),
    ("dt_6_to_10s", "6 <= dt <= 10 sec"),
    ("dt_11_to_30s", "11 <= dt <= 30 sec"),
    ("dt_31_to_60s", "31 <= dt <= 60 sec"),
    ("dt_61_to_300s", "61 <= dt <= 300 sec"),
    ("dt_over_300s", "dt > 300 sec"),
    ("other_positive_dt", "other positive dt"),
)


def _is_location_stream(value: str) -> bool:
    folded = value.strip().casefold()
    tokens = set(folded.split("_"))
    return (
        folded in _LOCATION_STREAMS
        or "latlng" in folded
        or bool(
            tokens
            & {
                "gps",
                "lat",
                "latitude",
                "lng",
                "lon",
                "longitude",
                "location",
            }
        )
    )


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    )


def _stream_code(value: Any) -> str | None:
    if value is None:
        return None
    rendered = str(value).strip()
    if not _STREAM_CODE_RE.fullmatch(rendered):
        return None
    compact = rendered.casefold()
    if any(token in compact for token in _SENSITIVE_STREAM_TOKENS):
        return None
    return rendered


def _point_container(value: Any) -> Sequence[Any]:
    if isinstance(value, Mapping):
        for key in ("data", "values", "points", "samples"):
            candidate = value.get(key)
            if _is_sequence(candidate):
                return candidate
        return ()
    return value if _is_sequence(value) else ()


def _iter_stream_inputs(streams: Any):
    """Yield safe stream codes and point containers without copying values."""

    if isinstance(streams, Mapping) and "streams" in streams:
        streams = streams.get("streams")

    if isinstance(streams, Mapping):
        if any(key in streams for key in ("data", "values", "points", "samples")):
            code = _stream_code(
                streams.get("type")
                or streams.get("stream")
                or streams.get("stream_name")
                or streams.get("name")
            )
            if code is not None:
                yield code, _point_container(streams)
            return

        for raw_code, raw_stream in streams.items():
            fallback = _stream_code(raw_code)
            if fallback is None:
                continue
            if isinstance(raw_stream, Mapping):
                code = _stream_code(
                    raw_stream.get("type")
                    or raw_stream.get("stream")
                    or raw_stream.get("stream_name")
                    or fallback
                )
            else:
                code = fallback
            if code is not None:
                yield code, _point_container(raw_stream)
        return

    if _is_sequence(streams):
        for raw_stream in streams:
            if not isinstance(raw_stream, Mapping):
                continue
            code = _stream_code(
                raw_stream.get("type")
                or raw_stream.get("stream")
                or raw_stream.get("stream_name")
                or raw_stream.get("name")
            )
            if code is not None:
                yield code, _point_container(raw_stream)


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        result = float(value)
    elif isinstance(value, str):
        try:
            result = float(value.strip())
        except (TypeError, ValueError):
            return None
    else:
        return None
    return result if math.isfinite(result) else None


def _rounded(value: float | None) -> float | None:
    if value is None:
        return None
    result = round(float(value), 6)
    return 0.0 if result == 0 else result


def _percent(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator * 100.0, 2)


def _classify_numeric(
    points: Sequence[Any],
) -> tuple[dict[str, Any], list[float], list[float | None]]:
    null_count = 0
    non_numeric_count = 0
    numeric: list[float] = []
    aligned: list[float | None] = []
    for value in points:
        if value is None:
            null_count += 1
            aligned.append(None)
            continue
        parsed = _number(value)
        if parsed is None:
            non_numeric_count += 1
            aligned.append(None)
            continue
        numeric.append(parsed)
        aligned.append(parsed)

    row: dict[str, Any] = {
        "point_count": len(points),
        "null_count": null_count,
        "non_numeric_count": non_numeric_count,
        "valid_numeric_count": len(numeric),
        "min": _rounded(min(numeric)) if numeric else None,
        "median": _rounded(median(numeric)) if numeric else None,
        "max": _rounded(max(numeric)) if numeric else None,
    }
    return row, numeric, aligned


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return _rounded(ordered[lower])
    fraction = position - lower
    return _rounded(
        ordered[lower] + (ordered[upper] - ordered[lower]) * fraction
    )


def _dt_bucket_key(delta: float) -> str | None:
    for second in range(1, 6):
        if math.isclose(delta, float(second), abs_tol=1e-9):
            return f"dt_eq_{second}s"
    if 6.0 <= delta <= 10.0:
        return "dt_6_to_10s"
    if 11.0 <= delta <= 30.0:
        return "dt_11_to_30s"
    if 31.0 <= delta <= 60.0:
        return "dt_31_to_60s"
    if 61.0 <= delta <= 300.0:
        return "dt_61_to_300s"
    if delta > 300.0:
        return "dt_over_300s"
    if delta > 0:
        return "other_positive_dt"
    return None


def _dt_distribution(deltas: Sequence[float]) -> dict[str, Any]:
    counts = Counter(
        key for delta in deltas if (key := _dt_bucket_key(delta)) is not None
    )
    positive = [delta for delta in deltas if delta > 0]
    denominator = len(deltas)
    return {
        "dt_definition": (
            "right_offset_sec - left_offset_sec for adjacent time-stream points"
        ),
        "percentage_denominator": "all_numeric_consecutive_dt_intervals",
        "percentile_population": "positive_consecutive_dt_intervals",
        "valid_interval_count": denominator,
        "positive_interval_count": len(positive),
        "buckets": [
            {
                "bucket": key,
                "label": label,
                "count": counts.get(key, 0),
                "percent": _percent(counts.get(key, 0), denominator),
            }
            for key, label in _DT_BUCKETS
        ],
        "percentiles_sec": {
            "p50": _percentile(positive, 0.50),
            "p75": _percentile(positive, 0.75),
            "p90": _percentile(positive, 0.90),
            "p95": _percentile(positive, 0.95),
            "p99": _percentile(positive, 0.99),
        },
    }


def _time_intervals(
    aligned_offsets: Sequence[float | None],
) -> list[dict[str, Any]]:
    intervals: list[dict[str, Any]] = []
    for left_index in range(len(aligned_offsets) - 1):
        start = aligned_offsets[left_index]
        end = aligned_offsets[left_index + 1]
        if start is None or end is None:
            continue
        intervals.append(
            {
                "left_index": left_index,
                "start_offset_sec": start,
                "end_offset_sec": end,
                "dt_sec": end - start,
            }
        )
    return intervals


def _first_available(
    records: Mapping[str, Mapping[str, Any]],
    aliases: Sequence[str],
) -> tuple[str | None, Mapping[str, Any] | None]:
    by_folded = {name.casefold(): name for name in records}
    for alias in aliases:
        name = by_folded.get(alias)
        if name is not None:
            return name, records[name]
    return None, None


def _frequency(deltas: Sequence[float]) -> float | None:
    if len(deltas) < 2:
        return None
    if not deltas or any(delta <= 0 for delta in deltas):
        return None
    mean_delta = sum(deltas) / len(deltas)
    if mean_delta <= 0:
        return None
    if len(deltas) > 1 and pstdev(deltas) / mean_delta > 0.15:
        return None
    typical_delta = median(deltas)
    if typical_delta <= 0:
        return None
    return _rounded(1.0 / typical_delta)


def _timing_report(
    name: str | None,
    record: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    aligned_offsets = (
        list(record.get("_aligned", ())) if record is not None else []
    )
    intervals = _time_intervals(aligned_offsets)
    deltas = [float(item["dt_sec"]) for item in intervals]
    offsets = [value for value in aligned_offsets if value is not None]
    rounded_deltas = [round(delta, 9) for delta in deltas]
    counts = Counter(rounded_deltas)
    mode_value: float | None = None
    if counts:
        maximum_count = max(counts.values())
        if maximum_count > 1 or len(counts) == 1:
            mode_value = min(
                delta for delta, count in counts.items() if count == maximum_count
            )

    positive_gaps = [
        delta for delta in deltas if delta > _GAP_THRESHOLD_SEC
    ]
    exactly_one = sum(
        1 for delta in deltas if math.isclose(delta, 1.0, abs_tol=1e-9)
    )
    stream_duration = (
        max(offsets) - min(offsets) if len(offsets) >= 2 else 0.0 if offsets else None
    )
    report = {
        "stream_present": record is not None,
        "stream_name": name,
        "point_count": int(record.get("point_count", 0)) if record else 0,
        "valid_offset_count": len(offsets),
        "null_offset_count": int(record.get("null_count", 0)) if record else 0,
        "non_numeric_offset_count": (
            int(record.get("non_numeric_count", 0)) if record else 0
        ),
        "dt_interval_count": len(deltas),
        "median_dt_sec": _rounded(median(deltas)) if deltas else None,
        "mode_dt_sec": _rounded(mode_value),
        "min_dt_sec": _rounded(min(deltas)) if deltas else None,
        "max_dt_sec": _rounded(max(deltas)) if deltas else None,
        "exactly_1s_interval_percent": _percent(exactly_one, len(deltas)),
        "repeated_offset_count": sum(1 for delta in deltas if delta == 0),
        "non_monotonic_offset_count": sum(1 for delta in deltas if delta < 0),
        "gap_count_over_1_5s": len(positive_gaps),
        "max_gap_sec": _rounded(max(positive_gaps)) if positive_gaps else None,
        "total_gap_time_sec": _rounded(sum(positive_gaps)) if positive_gaps else 0.0,
        "excess_gap_time_sec": (
            _rounded(sum(delta - 1.0 for delta in positive_gaps))
            if positive_gaps
            else 0.0
        ),
        "estimated_missing_seconds": sum(
            max(int(round(delta)) - 1, 0) for delta in positive_gaps
        ),
        "stream_duration_sec": _rounded(stream_duration),
        "estimated_frequency_hz": _frequency(deltas),
        "dt_distribution": _dt_distribution(deltas),
    }
    return report, intervals


def _safe_duration(detail: Mapping[str, Any], key: str) -> float | None:
    value = _number(detail.get(key))
    if value is None or value < 0:
        return None
    return _rounded(value)


def _duration_comparison(
    detail: Mapping[str, Any], stream_duration: float | None
) -> dict[str, Any]:
    result: dict[str, Any] = {"stream_duration_sec": stream_duration}
    for key in ("elapsed_time", "moving_time", "icu_recording_time"):
        duration = _safe_duration(detail, key)
        result[f"{key}_sec"] = duration
        result[f"{key}_minus_stream_sec"] = (
            _rounded(duration - stream_duration)
            if duration is not None and stream_duration is not None
            else None
        )
    return result


def _parse_absolute_epoch(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).timestamp()


def _activity_start_epoch(detail: Mapping[str, Any]) -> float | None:
    for key in ("start_date", "start_date_local"):
        parsed = _parse_absolute_epoch(detail.get(key))
        if parsed is not None:
            return parsed
    return None


def _duration_stats(durations: Sequence[float]) -> dict[str, Any]:
    if not durations:
        return {
            "total_duration_sec": 0.0,
            "min_duration_sec": None,
            "median_duration_sec": None,
            "max_duration_sec": None,
        }
    return {
        "total_duration_sec": _rounded(sum(durations)),
        "min_duration_sec": _rounded(min(durations)),
        "median_duration_sec": _rounded(median(durations)),
        "max_duration_sec": _rounded(max(durations)),
    }


def _gap_near_boundary(
    offset: float,
    gaps: Sequence[Mapping[str, Any]],
    *,
    tolerance_sec: float = 1.0,
) -> Mapping[str, Any] | None:
    candidates = [
        gap
        for gap in gaps
        if min(
            abs(offset - float(gap["start_offset_sec"])),
            abs(offset - float(gap["end_offset_sec"])),
        )
        <= tolerance_sec
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda gap: min(
            abs(offset - float(gap["start_offset_sec"])),
            abs(offset - float(gap["end_offset_sec"])),
        ),
    )


def _marker_candidates(
    markers: Sequence[int],
    gaps: Sequence[Mapping[str, Any]],
    detail: Mapping[str, Any],
) -> list[tuple[str, list[Mapping[str, Any]]]]:
    by_left_index = {int(gap["left_index"]): gap for gap in gaps}
    candidates: list[tuple[str, list[Mapping[str, Any]]]] = []

    for basis, shift in (
        ("stream_left_point_index", 0),
        ("stream_right_point_index", -1),
    ):
        matches = [
            gap
            for marker in markers
            if (gap := by_left_index.get(marker + shift)) is not None
        ]
        candidates.append((basis, matches))

    def match_offsets(basis: str, offsets: Sequence[float]) -> None:
        matches = [
            gap
            for offset in offsets
            if (gap := _gap_near_boundary(offset, gaps)) is not None
        ]
        candidates.append((basis, matches))

    match_offsets("relative_stream_offset_sec", [float(item) for item in markers])
    start_epoch = _activity_start_epoch(detail)
    if start_epoch is not None:
        match_offsets(
            "unix_epoch_sec",
            [float(item) - start_epoch for item in markers],
        )
        match_offsets(
            "unix_epoch_millisec",
            [float(item) / 1000.0 - start_epoch for item in markers],
        )
        match_offsets(
            "fit_epoch_sec",
            [
                float(item) + _FIT_EPOCH_UNIX_SEC - start_epoch
                for item in markers
            ],
        )
    return candidates


def _select_marker_matches(
    markers: Sequence[int],
    gaps: Sequence[Mapping[str, Any]],
    detail: Mapping[str, Any],
) -> tuple[str | None, list[Mapping[str, Any]]]:
    candidates = _marker_candidates(markers, gaps, detail)
    priority = {
        "stream_left_point_index": 5,
        "stream_right_point_index": 4,
        "unix_epoch_sec": 3,
        "unix_epoch_millisec": 2,
        "fit_epoch_sec": 1,
        "relative_stream_offset_sec": 0,
    }
    basis, matches = max(
        candidates,
        key=lambda item: (
            len(item[1]),
            sum(
                float(gap["dt_sec"]) > _LARGE_GAP_THRESHOLD_SEC
                for gap in item[1]
            ),
            priority[item[0]],
        ),
        default=(None, []),
    )
    return (basis, matches) if matches else (None, [])


def _relative_pair_endpoint(
    value: Any,
    *,
    detail: Mapping[str, Any],
    stream_duration_sec: float | None,
) -> float | None:
    absolute = _parse_absolute_epoch(value)
    start_epoch = _activity_start_epoch(detail)
    if absolute is not None and start_epoch is not None:
        return absolute - start_epoch

    numeric = _number(value)
    if numeric is None:
        return None
    candidates = [numeric]
    if start_epoch is not None:
        candidates.extend(
            (
                numeric - start_epoch,
                numeric / 1000.0 - start_epoch,
                numeric + _FIT_EPOCH_UNIX_SEC - start_epoch,
            )
        )
    maximum = stream_duration_sec if stream_duration_sec is not None else math.inf
    plausible = [item for item in candidates if -1.0 <= item <= maximum + 1.0]
    return plausible[0] if plausible else None


def _pair_values(value: Any) -> tuple[Any, Any] | None:
    if isinstance(value, Mapping):
        start = next(
            (
                value.get(key)
                for key in ("start", "start_time", "start_date")
                if key in value
            ),
            None,
        )
        end = next(
            (
                value.get(key)
                for key in ("end", "end_time", "end_date")
                if key in value
            ),
            None,
        )
        return (start, end) if start is not None and end is not None else None
    if _is_sequence(value) and len(value) == 2:
        return value[0], value[1]
    return None


def _recording_stops(
    detail: Mapping[str, Any],
    intervals: Sequence[Mapping[str, Any]],
    stream_duration_sec: float | None,
) -> tuple[dict[str, Any], set[int]]:
    gaps = [
        item
        for item in intervals
        if float(item["dt_sec"]) > _GAP_THRESHOLD_SEC
    ]
    outside_gap_indexes = {int(item["left_index"]) for item in gaps}
    raw = detail.get("recording_stops")
    present = "recording_stops" in detail and raw is not None

    base: dict[str, Any] = {
        "present": present,
        "structure_type": "missing" if not present else "unsupported",
        "structure_supported": not present,
        "count": 0,
        "valid_entry_count": 0,
        "invalid_entry_count": 0,
        "marker_interpretation": None,
        "start_end_mapping_status": "not_available",
        "mapped_stop_count": 0,
        "partially_overlapping_stop_count": 0,
        "unmatched_stop_count": 0,
        "matched_gap_count": 0,
        "matched_gap_total_time_sec": 0.0,
    }
    base.update(_duration_stats([]))

    matched_gap_indexes: set[int] = set()
    matched_durations: list[float] = []
    stop_durations: list[float] = []
    partial_count = 0
    valid_count = 0
    invalid_count = 0

    if not present:
        pass
    elif not _is_sequence(raw):
        scalar = _number(raw)
        if scalar == 0:
            base.update(
                {
                    "structure_type": "legacy_zero_count",
                    "structure_supported": True,
                    "count": 0,
                }
            )
        else:
            base.update(
                {
                    "count": 1,
                    "invalid_entry_count": 1,
                    "unmatched_stop_count": 1,
                }
            )
    else:
        base["count"] = len(raw)
        numeric_markers = [_number(item) for item in raw]
        is_integer_list = all(
            item is not None and float(item).is_integer()
            for item in numeric_markers
        )
        pairs = [_pair_values(item) for item in raw]
        is_pair_list = bool(raw) and all(pair is not None for pair in pairs)

        if is_integer_list:
            markers = [int(item) for item in numeric_markers if item is not None]
            valid_count = len(markers)
            basis, marker_matches = _select_marker_matches(markers, gaps, detail)
            for gap in marker_matches:
                matched_gap_indexes.add(int(gap["left_index"]))
            matched_durations = [
                float(gap["dt_sec"])
                for gap in gaps
                if int(gap["left_index"]) in matched_gap_indexes
            ]
            stop_durations = list(matched_durations)
            base.update(
                {
                    "structure_type": "integer_marker_list",
                    "structure_supported": True,
                    "marker_interpretation": basis,
                    "mapped_stop_count": len(marker_matches),
                    "unmatched_stop_count": len(markers) - len(marker_matches),
                }
            )

        elif is_pair_list:
            valid_pairs: list[tuple[float, float]] = []
            for pair in pairs:
                if pair is None:
                    invalid_count += 1
                    continue
                start = _relative_pair_endpoint(
                    pair[0], detail=detail, stream_duration_sec=stream_duration_sec
                )
                end = _relative_pair_endpoint(
                    pair[1], detail=detail, stream_duration_sec=stream_duration_sec
                )
                if start is None or end is None or end <= start:
                    invalid_count += 1
                    continue
                valid_pairs.append((start, end))

            valid_count = len(valid_pairs)
            exact_count = 0
            for start, end in valid_pairs:
                exact = next(
                    (
                        gap
                        for gap in gaps
                        if abs(start - float(gap["start_offset_sec"])) <= 1.0
                        and abs(end - float(gap["end_offset_sec"])) <= 1.0
                    ),
                    None,
                )
                if exact is not None:
                    exact_count += 1
                    matched_gap_indexes.add(int(exact["left_index"]))
                    continue
                if any(
                    min(end, float(gap["end_offset_sec"]))
                    > max(start, float(gap["start_offset_sec"]))
                    for gap in gaps
                ):
                    partial_count += 1

            matched_durations = [
                float(gap["dt_sec"])
                for gap in gaps
                if int(gap["left_index"]) in matched_gap_indexes
            ]
            stop_durations = list(matched_durations)
            base.update(
                {
                    "structure_type": "start_end_pair_list",
                    "structure_supported": valid_count > 0,
                    "marker_interpretation": "relative_or_convertible_start_end",
                    "mapped_stop_count": exact_count,
                    "partially_overlapping_stop_count": partial_count,
                    "unmatched_stop_count": len(raw) - exact_count - partial_count,
                }
            )
        else:
            invalid_count = len(raw)
            base.update(
                {
                    "invalid_entry_count": invalid_count,
                    "unmatched_stop_count": len(raw),
                }
            )

    if matched_gap_indexes:
        outside_gap_indexes -= matched_gap_indexes
    outside_gaps = [
        gap
        for gap in gaps
        if int(gap["left_index"]) in outside_gap_indexes
    ]
    outside_large_gaps = [
        gap
        for gap in outside_gaps
        if float(gap["dt_sec"]) > _LARGE_GAP_THRESHOLD_SEC
    ]
    mapped_count = int(base["mapped_stop_count"])
    stop_count = int(base["count"])
    if stop_count == 0:
        mapping_status = "not_applicable"
    elif mapped_count == stop_count:
        mapping_status = "all"
    elif mapped_count > 0 or partial_count > 0:
        mapping_status = "partial"
    else:
        mapping_status = "none"

    base.update(
        {
            "valid_entry_count": valid_count,
            "invalid_entry_count": invalid_count,
            "start_end_mapping_status": mapping_status,
            "matched_gap_count": len(matched_gap_indexes),
            "matched_gap_total_time_sec": _rounded(sum(matched_durations)) or 0.0,
            "outside_recording_stop_gap_count": len(outside_gaps),
            "outside_recording_stop_gap_total_time_sec": (
                _rounded(sum(float(item["dt_sec"]) for item in outside_gaps))
                or 0.0
            ),
            "outside_recording_stop_large_gap_count": len(outside_large_gaps),
            "outside_recording_stop_large_gap_total_time_sec": (
                _rounded(
                    sum(float(item["dt_sec"]) for item in outside_large_gaps)
                )
                or 0.0
            ),
        }
    )
    if stop_durations or stop_count == 0:
        base.update(_duration_stats(stop_durations))
    else:
        base.update(
            {
                "total_duration_sec": None,
                "min_duration_sec": None,
                "median_duration_sec": None,
                "max_duration_sec": None,
            }
        )
    return base, matched_gap_indexes


def _metric_coverage(
    records: Mapping[str, Mapping[str, Any]], aliases: Sequence[str]
) -> dict[str, Any]:
    alias_set = set(aliases)
    matching = [
        record
        for name, record in records.items()
        if name.casefold() in alias_set
    ]
    if not matching:
        return {
            "available": False,
            "stream_names": [],
            "best_coverage_percent": None,
        }
    return {
        "available": True,
        "stream_names": sorted(str(record["stream_name"]) for record in matching),
        "best_coverage_percent": max(
            float(record.get("coverage_percent", 0.0)) for record in matching
        ),
    }


def _moving_status(
    moving_points: Sequence[Any] | None,
    reference_count: int,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    if moving_points is not None:
        valid = sum(isinstance(value, bool) for value in moving_points)
        missing = sum(value is None for value in moving_points) + max(
            reference_count - len(moving_points), 0
        )
        if valid:
            return (
                {
                    "available": True,
                    "source": "moving_stream",
                    "valid_boolean_count": valid,
                    "missing_count": missing,
                    "coverage_percent": _percent(
                        valid, max(reference_count, len(moving_points))
                    ),
                },
                [],
            )
    return (
        {
            "available": False,
            "source": None,
            "valid_boolean_count": 0,
            "missing_count": reference_count,
            "coverage_percent": 0.0,
        },
        [
            {
                "code": "moving_status_unavailable",
                "message": (
                    "Moving status е unavailable: няма надежден експлицитен "
                    "moving stream и скоростта не се преобразува в moving state."
                ),
            }
        ],
    )


def _recording_segments(
    aligned_offsets: Sequence[float | None],
    intervals: Sequence[Mapping[str, Any]],
    matched_stop_gap_indexes: set[int],
) -> dict[str, Any]:
    segments: list[list[tuple[int, float]]] = []
    current: list[tuple[int, float]] = []

    def finish() -> None:
        nonlocal current
        if current:
            segments.append(current)
            current = []

    for index, offset in enumerate(aligned_offsets):
        if offset is None:
            finish()
            continue
        if current:
            previous_index, previous_offset = current[-1]
            must_break = (
                index != previous_index + 1
                or previous_index in matched_stop_gap_indexes
                or offset <= previous_offset
            )
            if must_break:
                finish()
        current.append((index, offset))
    finish()

    durations = [segment[-1][1] - segment[0][1] for segment in segments]
    point_counts = [len(segment) for segment in segments]
    included_deltas = [
        float(item["dt_sec"])
        for item in intervals
        if int(item["left_index"]) not in matched_stop_gap_indexes
        and float(item["dt_sec"]) > 0
    ]
    total_duration = sum(durations)
    effective_frequency = (
        sum(max(count - 1, 0) for count in point_counts) / total_duration
        if total_duration > 0
        else None
    )
    return {
        "segment_count": len(segments),
        "min_duration_sec": _rounded(min(durations)) if durations else None,
        "median_duration_sec": (
            _rounded(median(durations)) if durations else None
        ),
        "max_duration_sec": _rounded(max(durations)) if durations else None,
        "total_duration_sec": _rounded(total_duration) if durations else 0.0,
        "min_point_count": min(point_counts) if point_counts else 0,
        "median_point_count": (
            _rounded(median(point_counts)) if point_counts else None
        ),
        "max_point_count": max(point_counts) if point_counts else 0,
        "total_point_count": sum(point_counts),
        "effective_average_frequency_hz": _rounded(effective_frequency),
        "dt_distribution": _dt_distribution(included_deltas),
    }


def _speed_states(points: Sequence[Any]) -> list[str]:
    states: list[str] = []
    for value in points:
        if value is None:
            states.append("null")
            continue
        numeric = _number(value)
        if numeric is None:
            states.append("invalid")
        elif numeric < 0:
            states.append("negative")
        elif numeric == 0:
            states.append("zero")
        else:
            states.append("positive")
    return states


def _speed_diagnostics(
    name: str | None,
    record: Mapping[str, Any] | None,
    reference_count: int,
    intervals: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    source_states = list(record.get("_states", ())) if record else []
    denominator = max(reference_count, len(source_states))
    states = source_states + ["null"] * max(reference_count - len(source_states), 0)
    counts = Counter(states)
    valid_count = counts["zero"] + counts["positive"]
    bucket_rows: list[dict[str, Any]] = []
    for bucket, label in _DT_BUCKETS:
        relevant = [
            item
            for item in intervals
            if _dt_bucket_key(float(item["dt_sec"])) == bucket
        ]
        positive_both = 0
        at_least_one_zero = 0
        at_least_one_null = 0
        at_least_one_invalid = 0
        for item in relevant:
            left = int(item["left_index"])
            left_state = states[left] if left < len(states) else "null"
            right_state = states[left + 1] if left + 1 < len(states) else "null"
            endpoint_states = {left_state, right_state}
            positive_both += left_state == right_state == "positive"
            at_least_one_zero += "zero" in endpoint_states
            at_least_one_null += "null" in endpoint_states
            at_least_one_invalid += bool(
                endpoint_states & {"invalid", "negative"}
            )
        bucket_rows.append(
            {
                "bucket": bucket,
                "label": label,
                "interval_count": len(relevant),
                "positive_speed_at_both_endpoints_count": positive_both,
                "at_least_one_zero_speed_count": at_least_one_zero,
                "at_least_one_null_speed_count": at_least_one_null,
                "at_least_one_invalid_speed_count": at_least_one_invalid,
            }
        )

    return {
        "stream_present": record is not None,
        "stream_name": name,
        "reference_point_count": reference_count,
        "point_count": int(record.get("point_count", 0)) if record else 0,
        "coverage_percent": _percent(valid_count, denominator),
        "null_count": counts["null"],
        "null_percent": _percent(counts["null"], denominator),
        "zero_count": counts["zero"],
        "zero_percent": _percent(counts["zero"], denominator),
        "positive_count": counts["positive"],
        "positive_percent": _percent(counts["positive"], denominator),
        "invalid_count": counts["invalid"] + counts["negative"],
        "invalid_percent": _percent(
            counts["invalid"] + counts["negative"], denominator
        ),
        "missing_count": counts["null"],
        "non_numeric_count": counts["invalid"],
        "negative_count": counts["negative"],
        "dt_buckets": bucket_rows,
    }


def _duration_reconciliation(
    detail: Mapping[str, Any],
    stream_duration_sec: float | None,
    recording_stops: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    elapsed = _safe_duration(detail, "elapsed_time")
    recording = _safe_duration(detail, "icu_recording_time")
    moving = _safe_duration(detail, "moving_time")
    stop_total = _number(recording_stops.get("total_duration_sec"))
    matched_gap_total = _number(
        recording_stops.get("matched_gap_total_time_sec")
    )
    scale = max(
        elapsed or 0.0,
        stream_duration_sec or 0.0,
        recording or 0.0,
        1.0,
    )
    tolerance = _rounded(max(2.0, scale * 0.001)) or 2.0

    elapsed_minus_recording = (
        elapsed - recording
        if elapsed is not None and recording is not None
        else None
    )
    recording_minus_moving = (
        recording - moving
        if recording is not None and moving is not None
        else None
    )
    elapsed_recording_minus_stops = (
        elapsed_minus_recording - stop_total
        if elapsed_minus_recording is not None and stop_total is not None
        else None
    )
    stream_minus_explained = (
        stream_duration_sec - (recording + matched_gap_total)
        if stream_duration_sec is not None
        and recording is not None
        and matched_gap_total is not None
        else None
    )

    warnings: list[dict[str, str]] = []
    if not recording_stops.get("structure_supported", False):
        warnings.append(
            {
                "code": "recording_stop_structure_unresolved",
                "message": (
                    "Структурата на recording_stops не може да бъде "
                    "съпоставена безопасно с time stream-а."
                ),
            }
        )
    elif (
        int(recording_stops.get("count", 0)) > 0
        and stop_total is None
    ):
        warnings.append(
            {
                "code": "recording_stop_duration_unavailable",
                "message": (
                    "Recording-stop маркерите не съвпадат надеждно с gaps; "
                    "stop продължителност не е изведена."
                ),
            }
        )
    if (
        elapsed_recording_minus_stops is not None
        and abs(elapsed_recording_minus_stops) > tolerance
    ):
        warnings.append(
            {
                "code": "recording_stop_time_mismatch",
                "message": (
                    "Elapsed minus recording time не се обяснява от "
                    "агрегираната recording-stop продължителност."
                ),
            }
        )
    if stream_minus_explained is not None and abs(stream_minus_explained) > tolerance:
        warnings.append(
            {
                "code": "stream_explained_time_mismatch",
                "message": (
                    "Stream duration не се обяснява от recording time плюс "
                    "съвпадащите stop gaps."
                ),
            }
        )
    if int(recording_stops.get("outside_recording_stop_large_gap_count", 0)) > 0:
        warnings.append(
            {
                "code": "unexplained_large_gaps",
                "message": (
                    "Има gaps над 30 секунди, които не съвпадат с "
                    "recording_stops."
                ),
            }
        )

    result = {
        "tolerance_sec": tolerance,
        "elapsed_time_sec": elapsed,
        "stream_duration_sec": stream_duration_sec,
        "icu_recording_time_sec": recording,
        "moving_time_sec": moving,
        "recording_stop_total_duration_sec": _rounded(stop_total),
        "matched_stop_gap_total_duration_sec": _rounded(matched_gap_total),
        "elapsed_minus_icu_recording_sec": _rounded(elapsed_minus_recording),
        "icu_recording_minus_moving_sec": _rounded(recording_minus_moving),
        "elapsed_recording_difference_minus_stop_time_sec": _rounded(
            elapsed_recording_minus_stops
        ),
        "stream_minus_recording_plus_matched_stops_sec": _rounded(
            stream_minus_explained
        ),
        "within_tolerance": not any(
            warning["code"]
            in {"recording_stop_time_mismatch", "stream_explained_time_mismatch"}
            for warning in warnings
        ),
    }
    return result, warnings


def analyze_stream_quality(activity_detail: Any, streams: Any) -> dict[str, Any]:
    """Return aggregate, value-level diagnostics for one selected activity.

    The function never returns activity/athlete identifiers, OAuth material,
    absolute timestamps, raw points, names, API payloads, or coordinates.
    Location streams are discarded before any value analysis and are not
    represented by code or point values in the returned report.
    """

    detail = activity_detail if isinstance(activity_detail, Mapping) else {}
    records: dict[str, dict[str, Any]] = {}
    seen_stream_codes: set[str] = set()
    moving_points: Sequence[Any] | None = None
    duplicate_stream_entries = 0
    location_stream_excluded_count = 0

    for name, points in _iter_stream_inputs(streams):
        folded = name.casefold()
        if folded in seen_stream_codes:
            duplicate_stream_entries += 1
            continue
        seen_stream_codes.add(folded)

        point_count = len(points)
        if _is_location_stream(folded):
            location_stream_excluded_count += 1
            # Do not inspect data/data2 or retain the location stream code.
            del points
            continue
        if folded in _ABSOLUTE_TIME_STREAMS:
            records[name] = {
                "stream_name": name,
                "point_count": point_count,
                "absolute_timestamp_values_excluded": True,
            }
            del points
            continue
        if folded == "id" or folded.endswith("_id"):
            records[name] = {
                "stream_name": name,
                "point_count": point_count,
                "identifier_values_excluded": True,
            }
            del points
            continue

        row, numeric, aligned = _classify_numeric(points)
        row["stream_name"] = name
        if folded in set(_TIME_STREAMS + _HR_STREAMS + _SPEED_STREAMS):
            row["_numeric"] = numeric
        if folded in _TIME_STREAMS:
            row["_aligned"] = aligned
        if folded in _SPEED_STREAMS:
            row["_states"] = _speed_states(points)
        if folded == "moving":
            moving_points = points
        records[name] = row

    point_counts = [int(record.get("point_count", 0)) for record in records.values()]
    distinct_lengths = sorted(set(point_counts))
    time_name, time_record = _first_available(records, _TIME_STREAMS)
    reference_count = (
        int(time_record.get("point_count", 0))
        if time_record is not None
        else max(point_counts, default=0)
    )

    for record in records.values():
        if "valid_numeric_count" not in record:
            continue
        denominator = max(reference_count, int(record["point_count"]))
        record["missing_aligned_point_count"] = max(
            reference_count - int(record["point_count"]), 0
        )
        record["excess_point_count"] = max(
            int(record["point_count"]) - reference_count, 0
        )
        record["coverage_percent"] = _percent(
            int(record["valid_numeric_count"]), denominator
        )

    timing, intervals = _timing_report(time_name, time_record)
    recording_stops, matched_stop_gap_indexes = _recording_stops(
        detail,
        intervals,
        timing["stream_duration_sec"],
    )
    aligned_offsets = (
        list(time_record.get("_aligned", ())) if time_record else []
    )
    recording_segments = _recording_segments(
        aligned_offsets,
        intervals,
        matched_stop_gap_indexes,
    )
    hr_name, hr_record = _first_available(records, _HR_STREAMS)
    if hr_record is None:
        heart_rate = {
            "stream_present": False,
            "stream_name": None,
            "reference_point_count": reference_count,
            "point_count": 0,
            "coverage_percent": 0.0,
            "missing_count": reference_count,
            "non_numeric_count": 0,
            "non_positive_count": 0,
            "obviously_implausible_count": 0,
            "usable_count": 0,
            "plausible_min_bpm": _HR_PLAUSIBLE_MIN,
            "plausible_max_bpm": _HR_PLAUSIBLE_MAX,
        }
    else:
        hr_numeric = list(hr_record.get("_numeric", ()))
        non_positive = sum(value <= 0 for value in hr_numeric)
        implausible = sum(
            value > 0
            and (value < _HR_PLAUSIBLE_MIN or value > _HR_PLAUSIBLE_MAX)
            for value in hr_numeric
        )
        usable = len(hr_numeric) - non_positive - implausible
        denominator = max(reference_count, int(hr_record["point_count"]))
        heart_rate = {
            "stream_present": True,
            "stream_name": hr_name,
            "reference_point_count": reference_count,
            "point_count": int(hr_record["point_count"]),
            "coverage_percent": _percent(usable, denominator),
            "missing_count": int(hr_record.get("null_count", 0))
            + max(reference_count - int(hr_record["point_count"]), 0),
            "non_numeric_count": int(hr_record.get("non_numeric_count", 0)),
            "non_positive_count": non_positive,
            "obviously_implausible_count": implausible,
            "usable_count": usable,
            "plausible_min_bpm": _HR_PLAUSIBLE_MIN,
            "plausible_max_bpm": _HR_PLAUSIBLE_MAX,
        }

    speed_name, speed_record = _first_available(records, _SPEED_STREAMS)
    speed = _speed_diagnostics(
        speed_name,
        speed_record,
        reference_count,
        intervals,
    )

    moving_status, warnings = _moving_status(moving_points, reference_count)
    duration_reconciliation, reconciliation_warnings = _duration_reconciliation(
        detail,
        timing["stream_duration_sec"],
        recording_stops,
    )
    warnings.extend(reconciliation_warnings)

    public_streams: list[dict[str, Any]] = []
    for name in sorted(records, key=str.casefold):
        public_streams.append(
            {
                key: value
                for key, value in records[name].items()
                if not key.startswith("_")
            }
        )

    # Explicitly discard every retained numeric aggregate source before return.
    for record in records.values():
        record.pop("_numeric", None)
        record.pop("_aligned", None)
        record.pop("_states", None)
    del records, moving_points

    return {
        "schema_version": 2,
        "available_stream_names": [
            row["stream_name"] for row in public_streams
        ],
        "stream_count": len(public_streams),
        "location_stream_excluded_count": location_stream_excluded_count,
        "duplicate_stream_entry_count": duplicate_stream_entries,
        "stream_lengths": {
            "all_equal": len(distinct_lengths) <= 1,
            "distinct_point_counts": distinct_lengths,
            "min_point_count": min(point_counts) if point_counts else 0,
            "max_point_count": max(point_counts) if point_counts else 0,
            "reference_point_count": reference_count,
        },
        "streams": public_streams,
        "timing": timing,
        "duration_comparison": _duration_comparison(
            detail, timing["stream_duration_sec"]
        ),
        "recording_stops": recording_stops,
        "recording_segments": recording_segments,
        "duration_reconciliation": duration_reconciliation,
        "heart_rate": heart_rate,
        "speed": speed,
        "metric_coverage": {
            "cadence": _metric_coverage(
                {str(row["stream_name"]): row for row in public_streams},
                _CADENCE_STREAMS,
            ),
            "altitude": _metric_coverage(
                {str(row["stream_name"]): row for row in public_streams},
                _ALTITUDE_STREAMS,
            ),
            "power": _metric_coverage(
                {str(row["stream_name"]): row for row in public_streams},
                _POWER_STREAMS,
            ),
        },
        "moving_status": moving_status,
        "warnings": warnings,
    }


_FORBIDDEN_EXPORT_KEYS = {
    "access_token",
    "activity_detail",
    "activity_id",
    "athlete_id",
    "authorization",
    "client_secret",
    "data",
    "data2",
    "date",
    "end_date",
    "full_api_payload",
    "gps",
    "id",
    "latitude",
    "latlng",
    "longitude",
    "materialized_points",
    "name",
    "normalized_points",
    "oauth_token",
    "payload",
    "points",
    "raw_points",
    "refresh_token",
    "samples",
    "segment_slices",
    "source_index",
    "start_date",
    "start_date_local",
    "timestamp",
    "timestamps",
    "token",
    "values",
    "intervals",
    "one_hz_points",
}

_QUALITY_EXPORT_TOP_KEYS = {
    "available_stream_names",
    "duplicate_stream_entry_count",
    "duration_comparison",
    "duration_reconciliation",
    "heart_rate",
    "location_stream_excluded_count",
    "metric_coverage",
    "moving_status",
    "normalizer",
    "onflows_load_analysis",
    "onflows_zone_profile",
    "recording_segments",
    "recording_stops",
    "schema_version",
    "speed",
    "stream_count",
    "stream_lengths",
    "streams",
    "timing",
    "warnings",
    "zone_analysis",
}


def _forbidden_export_key(key: Any) -> bool:
    rendered = str(key).casefold()
    return (
        rendered in _FORBIDDEN_EXPORT_KEYS
        or "qref" in rendered
        or rendered
        in {
            "q",
            "q_z",
            "q_min",
            "weighted_seconds",
            "weighted_minutes",
            "total_weighted_sec",
            "average_k",
            "overall_average_k",
        }
        or rendered.endswith("_q_z")
        or rendered.endswith("_id")
        or rendered.startswith("raw_")
        or any(
            token in rendered
            for token in (
                "authorization",
                "client_secret",
                "credential",
                "oauth_token",
                "payload",
                "refresh_token",
            )
        )
    )


def _safe_export_copy(value: Any, *, key: str | None = None) -> Any:
    if key is not None and _forbidden_export_key(key):
        return None
    if isinstance(value, Mapping):
        return {
            str(child_key): cleaned
            for child_key, child in value.items()
            if not _forbidden_export_key(child_key)
            if (cleaned := _safe_export_copy(child, key=str(child_key))) is not None
        }
    if _is_sequence(value):
        return [_safe_export_copy(item) for item in value]
    if isinstance(value, str) and _ISO_TIMESTAMP_RE.fullmatch(value.strip()):
        return None
    if isinstance(value, str) and _is_location_stream(value):
        return None
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return None


def _assert_safe_export_tree(value: Any) -> None:
    """Recursively verify that the sanitized export contains no private form."""

    if isinstance(value, Mapping):
        for child_key, child in value.items():
            if _forbidden_export_key(child_key):
                raise ValueError("unsafe key survived stream-quality export")
            _assert_safe_export_tree(child)
        return
    if _is_sequence(value):
        for child in value:
            _assert_safe_export_tree(child)
        return
    if isinstance(value, str):
        if _ISO_TIMESTAMP_RE.fullmatch(value.strip()):
            raise ValueError("absolute timestamp survived stream-quality export")
        if _is_location_stream(value):
            raise ValueError("location data survived stream-quality export")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite value survived stream-quality export")


def export_stream_quality_json(
    analysis: Mapping[str, Any],
    normalizer_summary: Mapping[str, Any] | None = None,
) -> str:
    """Serialize a quality analysis with an additional export safety pass."""

    projected = {
        key: analysis[key]
        for key in _QUALITY_EXPORT_TOP_KEYS
        if key in analysis
    }
    if isinstance(normalizer_summary, Mapping):
        separate_aggregates = (
            "zone_analysis",
            "onflows_load_analysis",
            "onflows_zone_profile",
        )
        projected["normalizer"] = {
            key: value
            for key, value in normalizer_summary.items()
            if key not in separate_aggregates
        }
        for aggregate_key in separate_aggregates:
            aggregate = normalizer_summary.get(aggregate_key)
            if isinstance(aggregate, Mapping):
                projected[aggregate_key] = aggregate
    safe = _safe_export_copy(projected)
    _assert_safe_export_tree(safe)
    return json.dumps(
        safe,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    )
