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

    timing = dict(report["timing"])
    distribution = timing.pop("dt_distribution")
    assert timing == {
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
    assert distribution["dt_definition"] == (
        "right_offset_sec - left_offset_sec for adjacent time-stream points"
    )
    assert distribution["buckets"][0]["count"] == 4
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

    assert report["recording_stops"]["present"] is True
    assert report["recording_stops"]["count"] == 2
    assert report["recording_stops"]["invalid_entry_count"] == 2
    assert report["recording_stops"]["total_duration_sec"] is None
    assert report["moving_status"]["available"] is False
    assert report["warnings"][0]["code"] == "moving_status_unavailable"
    assert any(
        warning["code"] == "recording_stop_structure_unresolved"
        for warning in report["warnings"]
    )
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
        {
            "type": "gps_coordinates",
            "data": [latitude + 0.02, longitude + 0.02],
        },
    ]

    report = analyze_stream_quality({}, streams)
    exported = export_stream_quality_json(report)

    assert "latlng" not in _by_stream(report)
    assert "latlng" not in report["available_stream_names"]
    assert "gps_coordinates" not in _by_stream(report)
    assert report["location_stream_excluded_count"] == 2
    assert str(latitude) not in repr(report)
    assert str(longitude) not in repr(report)
    assert str(latitude) not in exported
    assert str(longitude) not in exported
    assert "latlng" not in exported.casefold()


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


def test_smart_recording_dt_one_to_five_seconds_is_bucketed() -> None:
    report = analyze_stream_quality(
        {},
        _streams(time=[0, 1, 3, 6, 10, 15]),
    )
    buckets = {
        row["bucket"]: row
        for row in report["timing"]["dt_distribution"]["buckets"]
    }

    for second in range(1, 6):
        assert buckets[f"dt_eq_{second}s"]["count"] == 1
        assert buckets[f"dt_eq_{second}s"]["percent"] == 20.0
    assert report["recording_segments"]["segment_count"] == 1


def test_all_dt_buckets_and_percentiles_are_reported() -> None:
    deltas = [1, 2, 3, 4, 5, 6, 10, 11, 30, 31, 60, 61, 300, 301]
    offsets = [0]
    for delta in deltas:
        offsets.append(offsets[-1] + delta)

    report = analyze_stream_quality({}, _streams(time=offsets))
    distribution = report["timing"]["dt_distribution"]
    counts = {
        row["bucket"]: row["count"] for row in distribution["buckets"]
    }

    assert counts == {
        "dt_eq_1s": 1,
        "dt_eq_2s": 1,
        "dt_eq_3s": 1,
        "dt_eq_4s": 1,
        "dt_eq_5s": 1,
        "dt_6_to_10s": 2,
        "dt_11_to_30s": 2,
        "dt_31_to_60s": 2,
        "dt_61_to_300s": 2,
        "dt_over_300s": 1,
        "other_positive_dt": 0,
    }
    assert distribution["percentiles_sec"] == {
        "p50": 10.5,
        "p75": 52.75,
        "p90": 228.3,
        "p95": 300.35,
        "p99": 300.87,
    }


def test_one_integer_recording_stop_matches_stream_gap() -> None:
    report = analyze_stream_quality(
        {
            "elapsed_time": 12,
            "icu_recording_time": 2,
            "moving_time": 2,
            "recording_stops": [1],
        },
        _streams(time=[0, 1, 11, 12]),
    )
    stops = report["recording_stops"]

    assert stops["structure_type"] == "integer_marker_list"
    assert stops["marker_interpretation"] == "stream_left_point_index"
    assert stops["start_end_mapping_status"] == "all"
    assert stops["mapped_stop_count"] == 1
    assert stops["matched_gap_count"] == 1
    assert stops["total_duration_sec"] == 10.0
    assert stops["min_duration_sec"] == 10.0
    assert stops["median_duration_sec"] == 10.0
    assert stops["max_duration_sec"] == 10.0


def test_multiple_recording_stops_create_multiple_recording_segments() -> None:
    report = analyze_stream_quality(
        {
            "elapsed_time": 33,
            "icu_recording_time": 3,
            "moving_time": 3,
            "recording_stops": [1, 3],
        },
        _streams(time=[0, 1, 11, 12, 32, 33]),
    )
    stops = report["recording_stops"]
    segments = report["recording_segments"]

    assert stops["count"] == 2
    assert stops["matched_gap_count"] == 2
    assert stops["total_duration_sec"] == 30.0
    assert stops["median_duration_sec"] == 15.0
    assert stops["outside_recording_stop_gap_count"] == 0
    assert segments["segment_count"] == 3
    assert segments["min_duration_sec"] == 1.0
    assert segments["median_duration_sec"] == 1.0
    assert segments["max_duration_sec"] == 1.0
    assert segments["total_duration_sec"] == 3.0
    assert segments["min_point_count"] == 2
    assert segments["median_point_count"] == 2.0
    assert segments["max_point_count"] == 2
    assert segments["total_point_count"] == 6
    assert segments["effective_average_frequency_hz"] == 1.0


def test_absolute_integer_stop_marker_is_mapped_without_exporting_timestamp() -> None:
    start_epoch = 1_700_000_000
    report = analyze_stream_quality(
        {
            "start_date": "2023-11-14T22:13:20Z",
            "recording_stops": [start_epoch + 1],
        },
        _streams(time=[0, 1, 11, 12]),
    )
    exported = export_stream_quality_json(report)

    assert report["recording_stops"]["marker_interpretation"] == "unix_epoch_sec"
    assert report["recording_stops"]["matched_gap_count"] == 1
    assert str(start_epoch + 1) not in repr(report)
    assert str(start_epoch + 1) not in exported
    assert "2023-11-14" not in exported


def test_partial_pair_overlap_is_not_treated_as_matched_stop_gap() -> None:
    report = analyze_stream_quality(
        {"recording_stops": [{"start": 4, "end": 8}]},
        _streams(time=[0, 1, 11, 12]),
    )
    stops = report["recording_stops"]

    assert stops["structure_type"] == "start_end_pair_list"
    assert stops["start_end_mapping_status"] == "partial"
    assert stops["partially_overlapping_stop_count"] == 1
    assert stops["matched_gap_count"] == 0
    assert stops["outside_recording_stop_gap_count"] == 1


def test_large_gap_without_recording_stop_remains_unexplained() -> None:
    report = analyze_stream_quality(
        {
            "elapsed_time": 102,
            "icu_recording_time": 2,
            "moving_time": 2,
        },
        _streams(time=[0, 1, 101, 102]),
    )
    codes = {warning["code"] for warning in report["warnings"]}

    assert report["recording_stops"]["matched_gap_count"] == 0
    assert report["recording_stops"]["outside_recording_stop_large_gap_count"] == 1
    assert "unexplained_large_gaps" in codes
    assert "stream_explained_time_mismatch" in codes


def test_speed_states_and_endpoint_counts_are_aggregated_by_dt_bucket() -> None:
    report = analyze_stream_quality(
        {},
        _streams(
            time=[0, 1, 3, 6, 10],
            velocity_smooth=[1.0, 0.0, None, 2.0, 3.0],
        ),
    )
    speed = report["speed"]
    buckets = {row["bucket"]: row for row in speed["dt_buckets"]}

    assert speed["null_count"] == 1
    assert speed["null_percent"] == 20.0
    assert speed["zero_count"] == 1
    assert speed["zero_percent"] == 20.0
    assert speed["positive_count"] == 3
    assert speed["positive_percent"] == 60.0
    assert speed["invalid_count"] == 0
    assert buckets["dt_eq_1s"]["at_least_one_zero_speed_count"] == 1
    assert buckets["dt_eq_2s"]["at_least_one_zero_speed_count"] == 1
    assert buckets["dt_eq_2s"]["at_least_one_null_speed_count"] == 1
    assert buckets["dt_eq_3s"]["at_least_one_null_speed_count"] == 1
    assert buckets["dt_eq_4s"]["positive_speed_at_both_endpoints_count"] == 1


def test_duration_reconciliation_explains_recording_stop_and_moving_time() -> None:
    report = analyze_stream_quality(
        {
            "elapsed_time": 12,
            "icu_recording_time": 2,
            "moving_time": 1,
            "recording_stops": [1],
        },
        _streams(time=[0, 1, 11, 12]),
    )
    reconciliation = report["duration_reconciliation"]

    assert reconciliation["elapsed_minus_icu_recording_sec"] == 10.0
    assert reconciliation["icu_recording_minus_moving_sec"] == 1.0
    assert reconciliation["elapsed_recording_difference_minus_stop_time_sec"] == 0.0
    assert reconciliation["stream_minus_recording_plus_matched_stops_sec"] == 0.0
    assert reconciliation["within_tolerance"] is True


def test_safe_export_rejects_full_payload_injection_and_location_stream() -> None:
    report = analyze_stream_quality(
        {"activity_id": "private-id"},
        [
            {"type": "time", "data": [0, 1]},
            {"type": "latlng", "data": [42.1, 42.2], "data2": [23.1, 23.2]},
        ],
    )
    report["full_api_payload"] = {
        "id": "private-id",
        "name": "Private Name",
        "access_token": "private-token",
        "data": [999_991, 999_992],
    }
    exported = export_stream_quality_json(report)

    for forbidden in (
        "private-id",
        "Private Name",
        "private-token",
        "999991",
        "999992",
        "42.1",
        "23.1",
        "latlng",
        "full_api_payload",
        "start_offset_sec",
        "end_offset_sec",
    ):
        assert forbidden not in exported


@pytest.mark.parametrize("recording_stops", [None, [], 0])
def test_recording_stop_presence_is_structural(recording_stops: Any) -> None:
    detail = {"recording_stops": recording_stops}
    report = analyze_stream_quality(detail, _streams(time=[0, 1]))

    expected_present = recording_stops is not None
    assert report["recording_stops"]["present"] is expected_present
    assert report["recording_stops"]["count"] == 0
