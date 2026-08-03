from __future__ import annotations

import json
from typing import Any

import pytest

from intervals_inspector.stream_quality import (
    analyze_stream_quality,
    export_stream_quality_json,
)


def _streams(**values: list[Any]) -> list[dict[str, Any]]:
    return [
        {"type": stream_name, "data": points}
        for stream_name, points in values.items()
    ]


def _by_stream(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row["stream_name"]): row
        for row in report["streams"]
    }


def test_uniform_one_hz_stream_reports_stable_timing_and_aggregates() -> None:
    report = analyze_stream_quality(
        {
            "elapsed_time": 4,
            "moving_time": 3,
            "icu_recording_time": 4,
        },
        _streams(
            time=[0, 1, 2, 3, 4],
            heartrate=[120, 121, 122, 123, 124],
            velocity_smooth=[0.0, 1.0, 2.0, 2.5, 3.0],
            cadence=[80, 81, 82, 83, 84],
            altitude=[500, 501, 502, 503, 504],
            watts=[180, 190, 200, 210, 220],
        ),
    )

    assert report["timing"] == {
        "stream_present": True,
        "stream_name": "time",
        "point_count": 5,
        "valid_offset_count": 5,
        "null_offset_count": 0,
        "non_numeric_offset_count": 0,
        "dt_interval_count": 4,
        "median_dt_sec": 1.0,
        "mode_dt_sec": 1.0,
        "min_dt_sec": 1.0,
        "max_dt_sec": 1.0,
        "exactly_1s_interval_percent": 100.0,
        "repeated_offset_count": 0,
        "non_monotonic_offset_count": 0,
        "gap_count_over_1_5s": 0,
        "max_gap_sec": None,
        "total_gap_time_sec": 0.0,
        "excess_gap_time_sec": 0.0,
        "estimated_missing_seconds": 0,
        "stream_duration_sec": 4.0,
        "estimated_frequency_hz": 1.0,
    }
    assert report["duration_comparison"]["elapsed_time_minus_stream_sec"] == 0.0
    assert report["heart_rate"]["coverage_percent"] == 100.0
    assert report["metric_coverage"]["cadence"]["best_coverage_percent"] == 100.0
    assert report["metric_coverage"]["altitude"]["available"] is True
    assert report["metric_coverage"]["power"]["available"] is True
    assert _by_stream(report)["watts"]["median"] == 200.0


def test_irregular_intervals_have_null_frequency_and_dt_range() -> None:
    report = analyze_stream_quality({}, _streams(time=[0, 0.7, 2.0, 3.8]))

    assert report["timing"]["estimated_frequency_hz"] is None
    assert report["timing"]["median_dt_sec"] == 1.3
    assert report["timing"]["mode_dt_sec"] is None
    assert report["timing"]["min_dt_sec"] == 0.7
    assert report["timing"]["max_dt_sec"] == 1.8
    assert report["timing"]["exactly_1s_interval_percent"] == 0.0


def test_missing_second_is_counted_as_gap_and_excess_time() -> None:
    report = analyze_stream_quality({}, _streams(time=[0, 1, 3, 4]))

    assert report["timing"]["gap_count_over_1_5s"] == 1
    assert report["timing"]["max_gap_sec"] == 2.0
    assert report["timing"]["total_gap_time_sec"] == 2.0
    assert report["timing"]["excess_gap_time_sec"] == 1.0
    assert report["timing"]["estimated_missing_seconds"] == 1


def test_long_gap_reports_full_and_excess_duration() -> None:
    report = analyze_stream_quality({}, _streams(time=[0, 1, 12, 13]))

    assert report["timing"]["gap_count_over_1_5s"] == 1
    assert report["timing"]["max_gap_sec"] == 11.0
    assert report["timing"]["total_gap_time_sec"] == 11.0
    assert report["timing"]["excess_gap_time_sec"] == 10.0
    assert report["timing"]["estimated_missing_seconds"] == 10


def test_pause_is_reported_only_as_recording_stop_count() -> None:
    report = analyze_stream_quality(
        {
            "recording_stops": [
                {"start": "private absolute timestamp", "end": "private"},
                {"start": "private", "end": "private"},
            ]
        },
        _streams(time=[0, 1, 5, 6], velocity_smooth=[1, 0, 0, 1]),
    )

    assert report["recording_stops"] == {"present": True, "count": 2}
    assert report["moving_status"]["available"] is False
    assert report["warnings"][0]["code"] == "moving_status_unavailable"
    assert "private" not in export_stream_quality_json(report)


def test_repeated_offset_is_counted_separately() -> None:
    report = analyze_stream_quality({}, _streams(time=[0, 1, 1, 2]))

    assert report["timing"]["repeated_offset_count"] == 1
    assert report["timing"]["non_monotonic_offset_count"] == 0
    assert report["timing"]["estimated_frequency_hz"] is None


def test_non_monotonic_time_is_counted_separately() -> None:
    report = analyze_stream_quality({}, _streams(time=[0, 2, 1, 3]))

    assert report["timing"]["repeated_offset_count"] == 0
    assert report["timing"]["non_monotonic_offset_count"] == 1
    assert report["timing"]["min_dt_sec"] == -1.0
    assert report["timing"]["estimated_frequency_hz"] is None


def test_missing_hr_is_explicit_and_uses_time_as_reference() -> None:
    report = analyze_stream_quality({}, _streams(time=[0, 1, 2, 3]))

    assert report["heart_rate"] == {
        "stream_present": False,
        "stream_name": None,
        "reference_point_count": 4,
        "point_count": 0,
        "coverage_percent": 0.0,
        "missing_count": 4,
        "non_numeric_count": 0,
        "non_positive_count": 0,
        "obviously_implausible_count": 0,
        "usable_count": 0,
        "plausible_min_bpm": 20.0,
        "plausible_max_bpm": 260.0,
    }


def test_null_invalid_non_positive_and_implausible_hr_are_separate() -> None:
    report = analyze_stream_quality(
        {},
        _streams(
            time=[0, 1, 2, 3, 4, 5, 6],
            heartrate=[120, None, "bad", 0, 10, 300, 140],
        ),
    )

    hr = report["heart_rate"]
    assert hr["missing_count"] == 1
    assert hr["non_numeric_count"] == 1
    assert hr["non_positive_count"] == 1
    assert hr["obviously_implausible_count"] == 2
    assert hr["usable_count"] == 2
    assert hr["coverage_percent"] == 28.57
    stream = _by_stream(report)["heartrate"]
    assert stream["null_count"] == 1
    assert stream["non_numeric_count"] == 1
    assert stream["valid_numeric_count"] == 5


def test_different_stream_lengths_and_alignment_missing_counts() -> None:
    report = analyze_stream_quality(
        {},
        _streams(
            time=[0, 1, 2, 3],
            heartrate=[120, 121, 122],
            velocity_smooth=[1.0, 2.0],
        ),
    )

    assert report["stream_lengths"] == {
        "all_equal": False,
        "distinct_point_counts": [2, 3, 4],
        "min_point_count": 2,
        "max_point_count": 4,
        "reference_point_count": 4,
    }
    assert report["heart_rate"]["missing_count"] == 1
    assert report["speed"]["missing_count"] == 2
    assert _by_stream(report)["heartrate"]["missing_aligned_point_count"] == 1


def test_missing_speed_and_negative_speed_are_explicit() -> None:
    absent = analyze_stream_quality({}, _streams(time=[0, 1, 2]))
    present = analyze_stream_quality(
        {},
        _streams(time=[0, 1, 2, 3], velocity_smooth=[1.0, -0.1, None, 2.0]),
    )

    assert absent["speed"]["stream_present"] is False
    assert absent["speed"]["missing_count"] == 3
    assert present["speed"]["negative_count"] == 1
    assert present["speed"]["missing_count"] == 1


def test_missing_power_is_explicit_without_synthesizing_values() -> None:
    report = analyze_stream_quality(
        {}, _streams(time=[0, 1, 2], cadence=[80, 81, 82])
    )

    assert report["metric_coverage"]["power"] == {
        "available": False,
        "stream_names": [],
        "best_coverage_percent": None,
    }


def test_latlng_coordinates_are_fully_excluded_but_presence_is_counted() -> None:
    latitude = 42.123456789
    longitude = 23.987654321
    streams = [
        {"type": "time", "data": [0, 1]},
        {
            "type": "latlng",
            "data": [latitude, latitude + 0.01],
            "data2": [longitude, longitude + 0.01],
        },
    ]

    report = analyze_stream_quality({}, streams)
    latlng = _by_stream(report)["latlng"]
    exported = export_stream_quality_json(report)

    assert latlng == {
        "stream_name": "latlng",
        "point_count": 2,
        "location_values_excluded": True,
    }
    assert "latlng" in report["available_stream_names"]
    assert str(latitude) not in repr(report)
    assert str(longitude) not in repr(report)
    assert str(latitude) not in exported
    assert str(longitude) not in exported


def test_export_has_no_ids_tokens_absolute_timestamps_or_raw_points() -> None:
    activity_detail = {
        "id": "private-activity-id",
        "athlete_id": "private-athlete-id",
        "name": "Private Athlete Activity",
        "start_date": "2026-08-03T07:08:09Z",
        "access_token": "private-oauth-token",
        "elapsed_time": 2,
    }
    streams = [
        {"type": "time", "data": [0, 1, 2]},
        {"type": "heartrate", "data": [101, 107, 109]},
        {
            "type": "timestamp",
            "data": [
                "2026-08-03T07:08:09Z",
                "2026-08-03T07:08:10Z",
                "2026-08-03T07:08:11Z",
            ],
        },
    ]
    report = analyze_stream_quality(activity_detail, streams)
    report["activity_id"] = "injected-private-id"
    report["raw_points"] = [99991, 99992]
    report["nested_injection"] = {
        "token": "injected-private-token",
        "timestamp": "2026-08-03T07:08:09Z",
        "data": [88881, 88882],
    }

    exported = export_stream_quality_json(report)
    parsed = json.loads(exported)

    for sensitive in (
        "private-activity-id",
        "private-athlete-id",
        "Private Athlete Activity",
        "private-oauth-token",
        "2026-08-03T07:08:09Z",
        "injected-private-id",
        "injected-private-token",
        "99991",
        "99992",
        "88881",
        "88882",
    ):
        assert sensitive not in exported
    assert parsed["timing"]["stream_duration_sec"] == 2.0
    assert _by_stream(parsed)["timestamp"] == {
        "stream_name": "timestamp",
        "point_count": 3,
        "absolute_timestamp_values_excluded": True,
    }


@pytest.mark.parametrize("recording_stops", [None, [], 0])
def test_recording_stop_presence_is_structural(recording_stops: Any) -> None:
    detail = {"recording_stops": recording_stops}
    report = analyze_stream_quality(detail, _streams(time=[0, 1]))

    expected_present = recording_stops is not None
    assert report["recording_stops"]["present"] is expected_present
    assert report["recording_stops"]["count"] == 0
