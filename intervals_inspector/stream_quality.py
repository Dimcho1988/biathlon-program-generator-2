"""In-memory, deidentified diagnostics for one activity's streams.

This module deliberately has no Streamlit, HTTP, OAuth, file-system, logging,
or persistence dependency.  It accepts transient API payloads and returns only
aggregate counts and statistics.  Location coordinates and absolute timestamp
values are never copied into the result.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
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


def _classify_numeric(points: Sequence[Any]) -> tuple[dict[str, Any], list[float]]:
    null_count = 0
    non_numeric_count = 0
    numeric: list[float] = []
    for value in points:
        if value is None:
            null_count += 1
            continue
        parsed = _number(value)
        if parsed is None:
            non_numeric_count += 1
            continue
        numeric.append(parsed)

    row: dict[str, Any] = {
        "point_count": len(points),
        "null_count": null_count,
        "non_numeric_count": non_numeric_count,
        "valid_numeric_count": len(numeric),
        "min": _rounded(min(numeric)) if numeric else None,
        "median": _rounded(median(numeric)) if numeric else None,
        "max": _rounded(max(numeric)) if numeric else None,
    }
    return row, numeric


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


def _frequency(offsets: Sequence[float]) -> float | None:
    if len(offsets) < 3:
        return None
    deltas = [offsets[index] - offsets[index - 1] for index in range(1, len(offsets))]
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
) -> dict[str, Any]:
    offsets = list(record.get("_numeric", ())) if record is not None else []
    deltas = [
        offsets[index] - offsets[index - 1]
        for index in range(1, len(offsets))
    ]
    rounded_deltas = [round(delta, 9) for delta in deltas]
    counts = Counter(rounded_deltas)
    mode_value: float | None = None
    if counts:
        maximum_count = max(counts.values())
        if maximum_count > 1 or len(counts) == 1:
            mode_value = min(
                delta for delta, count in counts.items() if count == maximum_count
            )

    positive_gaps = [delta for delta in deltas if delta > 1.5]
    exactly_one = sum(
        1 for delta in deltas if math.isclose(delta, 1.0, abs_tol=1e-9)
    )
    stream_duration = (
        max(offsets) - min(offsets) if len(offsets) >= 2 else 0.0 if offsets else None
    )
    return {
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
        "estimated_frequency_hz": _frequency(offsets),
    }


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


def _recording_stops(detail: Mapping[str, Any]) -> dict[str, Any]:
    if "recording_stops" not in detail or detail.get("recording_stops") is None:
        return {"present": False, "count": 0}
    raw = detail.get("recording_stops")
    if isinstance(raw, Mapping) or _is_sequence(raw):
        count = len(raw)
    else:
        numeric = _number(raw)
        count = max(int(numeric), 0) if numeric is not None else 1
    return {"present": True, "count": count}


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
                    "coverage_percent": _percent(valid, max(reference_count, len(moving_points))),
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


def analyze_stream_quality(activity_detail: Any, streams: Any) -> dict[str, Any]:
    """Return aggregate, value-level diagnostics for one selected activity.

    The function never returns activity/athlete identifiers, OAuth material,
    absolute timestamps, raw points, names, API payloads, or coordinates.
    ``latlng`` is represented only by its stream code and primary point count.
    """

    detail = activity_detail if isinstance(activity_detail, Mapping) else {}
    records: dict[str, dict[str, Any]] = {}
    seen_stream_codes: set[str] = set()
    moving_points: Sequence[Any] | None = None
    duplicate_stream_entries = 0

    for name, points in _iter_stream_inputs(streams):
        folded = name.casefold()
        if folded in seen_stream_codes:
            duplicate_stream_entries += 1
            continue
        seen_stream_codes.add(folded)

        point_count = len(points)
        if folded in _LOCATION_STREAMS:
            records[name] = {
                "stream_name": name,
                "point_count": point_count,
                "location_values_excluded": True,
            }
            # Do not inspect data2 or retain any coordinate-bearing sequence.
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

        row, numeric = _classify_numeric(points)
        row["stream_name"] = name
        if folded in set(_TIME_STREAMS + _HR_STREAMS + _SPEED_STREAMS):
            row["_numeric"] = numeric
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

    timing = _timing_report(time_name, time_record)
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
    if speed_record is None:
        speed = {
            "stream_present": False,
            "stream_name": None,
            "reference_point_count": reference_count,
            "point_count": 0,
            "coverage_percent": 0.0,
            "missing_count": reference_count,
            "non_numeric_count": 0,
            "negative_count": 0,
        }
    else:
        speed_numeric = list(speed_record.get("_numeric", ()))
        negative = sum(value < 0 for value in speed_numeric)
        denominator = max(reference_count, int(speed_record["point_count"]))
        speed = {
            "stream_present": True,
            "stream_name": speed_name,
            "reference_point_count": reference_count,
            "point_count": int(speed_record["point_count"]),
            "coverage_percent": _percent(len(speed_numeric) - negative, denominator),
            "missing_count": int(speed_record.get("null_count", 0))
            + max(reference_count - int(speed_record["point_count"]), 0),
            "non_numeric_count": int(speed_record.get("non_numeric_count", 0)),
            "negative_count": negative,
        }

    moving_status, warnings = _moving_status(moving_points, reference_count)

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
    del records, moving_points

    return {
        "schema_version": 1,
        "available_stream_names": [
            row["stream_name"] for row in public_streams
        ],
        "stream_count": len(public_streams),
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
        "recording_stops": _recording_stops(detail),
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
    "id",
    "latitude",
    "latlng",
    "longitude",
    "name",
    "oauth_token",
    "points",
    "raw_points",
    "refresh_token",
    "samples",
    "timestamp",
    "timestamps",
    "token",
    "values",
}


def _safe_export_copy(value: Any, *, key: str | None = None) -> Any:
    if key is not None and key.casefold() in _FORBIDDEN_EXPORT_KEYS:
        return None
    if isinstance(value, Mapping):
        return {
            str(child_key): cleaned
            for child_key, child in value.items()
            if str(child_key).casefold() not in _FORBIDDEN_EXPORT_KEYS
            if (cleaned := _safe_export_copy(child, key=str(child_key))) is not None
        }
    if _is_sequence(value):
        return [_safe_export_copy(item) for item in value]
    if isinstance(value, str) and _ISO_TIMESTAMP_RE.fullmatch(value.strip()):
        return None
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return None


def export_stream_quality_json(analysis: Mapping[str, Any]) -> str:
    """Serialize a quality analysis with an additional export safety pass."""

    safe = _safe_export_copy(analysis)
    return json.dumps(
        safe,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    )
