from __future__ import annotations

from copy import deepcopy
import json
import math
from typing import Any

import pytest

from intervals_inspector.stream_normalizer import (
    NormalizerInput,
    build_normalizer_input,
    build_normalizer_summary,
    materialize_1hz,
    normalize_stream_intervals,
)
from intervals_inspector.stream_quality import (
    analyze_stream_quality,
    export_stream_quality_json,
)


def _input(
    offsets: list[Any],
    *,
    metrics: dict[str, list[Any]] | None = None,
    stop_bounds: set[tuple[float, float]] | None = None,
    elapsed: float | None = None,
    recording: float | None = None,
    moving: float | None = None,
    stop_count: int = 0,
    unmatched_stops: int = 0,
) -> NormalizerInput:
    return NormalizerInput(
        offsets=offsets,
        metrics=metrics or {},
        recording_stop_bounds=frozenset(stop_bounds or set()),
        recording_stop_marker_count=stop_count,
        unmatched_recording_stop_marker_count=unmatched_stops,
        elapsed_time_sec=elapsed,
        icu_recording_time_sec=recording,
        moving_time_sec=moving,
    )


def _classes(result: Any) -> list[str]:
    return [interval.classification for interval in result.intervals]


def _values(result: Any, metric: str) -> list[float | None]:
    return [point.value(metric) for point in result.points]


def test_clean_one_hz_uses_fast_path_without_materialization() -> None:
    normalizer_input = _input(
        [0, 1, 2, 3, 4],
        metrics={
            "heartrate": [120, 121, 122, 123, 124],
            "velocity_smooth": [1, 2, 3, 4, 5],
        },
        elapsed=4,
        recording=4,
        moving=4,
    )

    result = normalize_stream_intervals(normalizer_input)
    summary = build_normalizer_summary(result)

    assert result.fast_path_used is True
    assert _classes(result) == ["original_1hz"] * 4
    assert [point.offset_sec for point in result.points] == [0, 1, 2, 3, 4]
    assert _values(result, "heartrate") == [120, 121, 122, 123, 124]
    assert summary["materialize_1hz"]["requested"] is False
    assert summary["materialize_1hz"]["point_count"] == 0
    assert summary["normalized_second_count_estimate"] == 5


def test_fast_path_materialization_reuses_original_point_tuple() -> None:
    result = normalize_stream_intervals(
        _input([0, 1, 2], metrics={"heartrate": [100, 101, 102]})
    )

    materialized = materialize_1hz(result)

    assert materialized.points is result.points
    assert materialized.reused_interval_points is True
    assert materialized.created_new_points is False
    assert [point.source for point in materialized.points] == [
        "original",
        "original",
        "original",
    ]


@pytest.mark.parametrize("dt_sec", [2, 3, 4, 5])
def test_short_smart_recording_can_be_materialized(dt_sec: int) -> None:
    result = normalize_stream_intervals(
        _input(
            [0, dt_sec],
            metrics={"heartrate": [100, 100 + 10 * dt_sec]},
        )
    )

    assert _classes(result) == ["smart_recording"]
    assert result.intervals[0].interpolation_allowed is True
    assert result.intervals[0].interpolation_source == "interpolated_short"
    materialized = materialize_1hz(result)
    assert [point.offset_sec for point in materialized.points] == list(
        range(dt_sec + 1)
    )
    assert sum(
        point.source == "interpolated_short"
        for point in materialized.points
    ) == dt_sec - 1
    assert materialized.active_duration_sec == dt_sec


@pytest.mark.parametrize("dt_sec", [6, 8, 10])
def test_extended_smart_recording_requires_positive_endpoint_speed(
    dt_sec: int,
) -> None:
    result = normalize_stream_intervals(
        _input(
            [0, dt_sec],
            metrics={"velocity_smooth": [1.0, 2.0]},
        )
    )

    interval = result.intervals[0]
    assert interval.classification == "smart_recording"
    assert interval.interpolation_source == "interpolated_extended"
    materialized = materialize_1hz(result)
    assert len(materialized.points) == dt_sec + 1
    assert sum(
        point.source == "interpolated_extended"
        for point in materialized.points
    ) == dt_sec - 1


@pytest.mark.parametrize(
    "speed, expected_flag",
    [
        ([1.0, 0.0], "zero_speed_endpoint"),
        ([1.0, None], "speed_endpoint_missing_or_invalid"),
    ],
)
def test_extended_smart_recording_is_blocked_without_positive_speed(
    speed: list[Any], expected_flag: str
) -> None:
    result = normalize_stream_intervals(
        _input([0, 8], metrics={"velocity_smooth": speed})
    )

    interval = result.intervals[0]
    assert interval.classification == "uncertain_gap"
    assert interval.interpolation_allowed is False
    assert expected_flag in interval.quality_flags
    assert materialize_1hz(result).points == ()


def test_eleven_to_thirty_seconds_is_uncertain_and_breaks_segment() -> None:
    result = normalize_stream_intervals(
        _input(
            [0, 1, 20, 21],
            metrics={"velocity_smooth": [1, 1, 1, 1]},
        )
    )

    assert _classes(result) == [
        "original_1hz",
        "uncertain_gap",
        "original_1hz",
    ]
    assert result.recording_segment_count == 2
    assert result.active_duration_sec == 2


def test_large_gap_with_zero_speed_is_probable_pause() -> None:
    result = normalize_stream_intervals(
        _input(
            [0, 60],
            metrics={"velocity_smooth": [0, 1]},
        )
    )

    assert _classes(result) == ["probable_pause"]
    assert result.active_duration_sec == 0
    assert materialize_1hz(result).points == ()


def test_large_gap_supported_by_duration_reconciliation_is_probable_pause() -> None:
    result = normalize_stream_intervals(
        _input([0, 60], elapsed=60, recording=0, moving=0)
    )

    assert _classes(result) == ["probable_pause"]
    assert "duration_reconciliation_pause_evidence" in (
        result.intervals[0].quality_flags
    )


def test_large_gap_without_evidence_is_technical_or_unexplained() -> None:
    result = normalize_stream_intervals(_input([0, 60]))

    assert _classes(result) == ["technical_or_unexplained_gap"]
    assert any(
        warning.code == "technical_or_unexplained_gaps_present"
        for warning in result.warnings
    )


def test_recording_stop_always_breaks_even_short_interval() -> None:
    result = normalize_stream_intervals(
        _input(
            [0, 4],
            stop_bounds={(0.0, 4.0)},
            stop_count=1,
        )
    )

    assert _classes(result) == ["recording_stop"]
    summary = build_normalizer_summary(result)
    assert summary["classifications"]["recording_stop"] == {
        "interval_count": 1,
        "duration_sec": 4.0,
    }
    assert materialize_1hz(result).points == ()


def test_unmatched_recording_stop_marker_disables_fast_path() -> None:
    result = normalize_stream_intervals(
        _input([0, 1, 2], stop_count=1, unmatched_stops=1)
    )

    assert _classes(result) == ["original_1hz", "original_1hz"]
    assert result.fast_path_used is False
    assert any(
        warning.code == "recording_stop_markers_unmatched"
        for warning in result.warnings
    )


def test_multiple_segments_materialize_without_gap_points() -> None:
    result = normalize_stream_intervals(
        _input(
            [0, 1, 10, 11],
            stop_bounds={(1.0, 10.0)},
            stop_count=1,
        )
    )
    materialized = materialize_1hz(result)

    assert result.recording_segment_count == 2
    assert result.active_duration_sec == 2
    assert [point.offset_sec for point in materialized.points] == [0, 1, 10, 11]
    assert materialized.segment_slices == ((0, 2), (2, 4))
    assert materialized.active_duration_sec == result.active_duration_sec


@pytest.mark.parametrize(
    "heartrate",
    [
        [100, None],
        [None, None],
    ],
)
def test_missing_metric_endpoints_are_not_invented(
    heartrate: list[Any],
) -> None:
    result = normalize_stream_intervals(
        _input([0, 3], metrics={"heartrate": heartrate})
    )
    materialized = materialize_1hz(result)

    assert materialized.points[1].value("heartrate") is None
    assert materialized.points[2].value("heartrate") is None
    assert "missing_value" in materialized.points[1].quality_flags


def test_invalid_metric_values_are_removed_structurally() -> None:
    result = normalize_stream_intervals(
        _input(
            [0, 1, 2, 3],
            metrics={
                "heartrate": [math.nan, 100, None, 120],
                "velocity_smooth": [-1, 0, "bad", math.inf],
            },
        )
    )
    summary = build_normalizer_summary(result)

    assert summary["invalid_values_by_metric"] == {
        "heartrate": 1,
        "velocity_smooth": 3,
    }
    assert result.points[0].value("heartrate") is None
    assert result.points[0].value("velocity_smooth") is None
    assert "invalid_source_value" in result.points[0].quality_flags


def test_duplicate_offsets_use_last_valid_value_per_metric() -> None:
    result = normalize_stream_intervals(
        _input(
            [0, 1, 1, 2],
            metrics={"heartrate": [100, 110, 111, 120]},
        )
    )

    assert result.duplicate_offset_count == 1
    assert [point.offset_sec for point in result.points] == [0, 1, 2]
    assert _values(result, "heartrate") == [100, 111, 120]
    assert "duplicate_offset" in result.points[1].quality_flags
    assert result.fast_path_used is False


def test_unsorted_offsets_use_stable_sort_once() -> None:
    result = normalize_stream_intervals(
        _input(
            [2, 0, 1],
            metrics={"heartrate": [120, 100, 110]},
        )
    )

    assert result.sorted_fallback_used is True
    assert [point.offset_sec for point in result.points] == [0, 1, 2]
    assert _values(result, "heartrate") == [100, 110, 120]
    assert any(
        warning.code == "input_sorted_by_offset"
        for warning in result.warnings
    )


def test_invalid_offsets_are_excluded_and_flagged() -> None:
    result = normalize_stream_intervals(
        _input(
            [-1, None, "bad", math.nan, 0, 1],
            metrics={"heartrate": [1, 2, 3, 4, 100, 101]},
        )
    )

    assert result.invalid_offset_count == 4
    assert [point.offset_sec for point in result.points] == [0, 1]
    assert _values(result, "heartrate") == [100, 101]


def test_materializer_does_not_extrapolate_before_or_after_input() -> None:
    result = normalize_stream_intervals(
        _input([5, 8], metrics={"heartrate": [100, 130]})
    )

    materialized = materialize_1hz(result)

    assert [point.offset_sec for point in materialized.points] == [5, 6, 7, 8]
    assert min(point.offset_sec for point in materialized.points) == 5
    assert max(point.offset_sec for point in materialized.points) == 8


def test_normalizer_does_not_mutate_original_input_and_is_deterministic() -> None:
    offsets = [3, 0, 1, 1]
    metrics = {
        "heartrate": [130, 100, 110, 111],
        "velocity_smooth": [2, 1, 1, 1],
    }
    original = {"offsets": deepcopy(offsets), "metrics": deepcopy(metrics)}
    normalizer_input = _input(offsets, metrics=metrics)

    first = normalize_stream_intervals(normalizer_input)
    second = normalize_stream_intervals(normalizer_input)

    assert offsets == original["offsets"]
    assert metrics == original["metrics"]
    assert first == second
    assert materialize_1hz(first) == materialize_1hz(second)


def test_reconciliation_warning_is_aggregate_only() -> None:
    result = normalize_stream_intervals(
        _input([0, 1], elapsed=100, recording=100, moving=90)
    )

    codes = {warning.code for warning in result.warnings}
    assert "active_recording_time_mismatch" in codes
    assert "elapsed_stream_time_mismatch" in codes


def test_partial_recording_stop_match_remains_unmatched() -> None:
    activity_detail = {
        "recording_stops": [{"start": 2, "end": 5}],
        "elapsed_time": 11,
    }
    streams = [
        {"type": "time", "data": [0, 1, 11]},
        {"type": "velocity_smooth", "data": [1, 1, 1]},
    ]

    normalizer_input = build_normalizer_input(activity_detail, streams)
    result = normalize_stream_intervals(normalizer_input)

    assert normalizer_input.recording_stop_marker_count == 1
    assert normalizer_input.unmatched_recording_stop_marker_count == 1
    assert "recording_stop" not in _classes(result)
    assert any(
        warning.code == "recording_stop_markers_unmatched"
        for warning in result.warnings
    )


def test_input_adapter_excludes_location_and_sensitive_streams_early() -> None:
    latitude = 42.123456
    longitude = 23.987654
    activity_detail = {
        "id": "private-activity-id",
        "athlete_id": "private-athlete-id",
        "name": "Private Activity",
        "type": "Run",
        "elapsed_time": 2,
    }
    streams = [
        {"type": "time", "data": [0, 1, 2]},
        {"type": "heartrate", "data": [100, 101, 102]},
        {"type": "latlng", "data": [latitude], "data2": [longitude]},
        {"type": "gps_coordinates", "data": [latitude, longitude]},
        {"type": "oauth_token", "data": ["private-token"]},
    ]
    original = deepcopy(streams)

    normalizer_input = build_normalizer_input(activity_detail, streams)
    result = normalize_stream_intervals(normalizer_input)
    rendered = repr(normalizer_input) + repr(result)

    assert normalizer_input.excluded_location_stream_count == 2
    assert set(normalizer_input.metrics) == {"heartrate"}
    assert "private-activity-id" not in rendered
    assert "private-athlete-id" not in rendered
    assert "Private Activity" not in rendered
    assert "private-token" not in rendered
    assert str(latitude) not in rendered
    assert str(longitude) not in rendered
    assert streams == original


def test_summary_has_no_intervals_points_or_individual_metric_values() -> None:
    result = normalize_stream_intervals(
        _input(
            [0, 3],
            metrics={
                "heartrate": [103.123456, 177.654321],
                "velocity_smooth": [1.234567, 3.765432],
            },
        )
    )
    materialized = materialize_1hz(result)
    summary = build_normalizer_summary(result, materialized)
    exported = json.dumps(summary, sort_keys=True)

    for forbidden in (
        "103.123456",
        "177.654321",
        "1.234567",
        "3.765432",
        "start_offset_sec",
        "end_offset_sec",
        '"points"',
        '"intervals"',
    ):
        assert forbidden not in exported
    assert summary["materialize_1hz"]["requested"] is True
    assert summary["materialize_1hz"]["point_count"] == 4
    assert summary["active_duration_sec"] == 3
    assert summary["materialize_1hz"]["active_duration_sec"] == 3


def test_combined_safe_export_recursively_removes_normalizer_internals() -> None:
    quality = analyze_stream_quality(
        {},
        [
            {"type": "time", "data": [0, 1, 2]},
            {"type": "heartrate", "data": [100, 101, 102]},
        ],
    )
    result = normalize_stream_intervals(
        _input([0, 2], metrics={"heartrate": [100, 102]})
    )
    summary = build_normalizer_summary(result, materialize_1hz(result))
    summary["intervals"] = [
        {
            "start_offset_sec": 0,
            "end_offset_sec": 2,
            "values": [999_991],
        }
    ]
    summary["materialized_points"] = [999_992]
    summary["activity_id"] = "private-activity-id"
    summary["token"] = "private-token"
    summary["timestamp"] = "2026-08-05T12:34:56Z"
    summary["latlng"] = [[42.1, 23.1]]

    exported = export_stream_quality_json(quality, summary)
    parsed = json.loads(exported)

    assert "normalizer" in parsed
    serialized_keys: set[str] = set()
    serialized_values: list[object] = []

    def collect_exported_content(value: object) -> None:
        if isinstance(value, dict):
            serialized_keys.update(str(key) for key in value)
            for nested in value.values():
                collect_exported_content(nested)
        elif isinstance(value, list):
            for nested in value:
                collect_exported_content(nested)
        else:
            serialized_values.append(value)

    collect_exported_content(parsed)
    for forbidden_key in (
        "activity_id",
        "token",
        "timestamp",
        "latlng",
        "intervals",
        "materialized_points",
    ):
        assert forbidden_key not in serialized_keys
    for forbidden_value in (
        "private-activity-id",
        "private-token",
        "2026-08-05T12:34:56Z",
        42.1,
        23.1,
        999_991,
        999_992,
    ):
        assert forbidden_value not in serialized_values
