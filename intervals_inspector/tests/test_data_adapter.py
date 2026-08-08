from __future__ import annotations

import pytest

from intervals_inspector.data_adapter import (
    MAX_SHADOW_PERIOD_DAYS,
    build_mapping_report,
    build_model_readiness,
    validate_shadow_period,
)


def _coverage(*paths: str) -> list[dict[str, object]]:
    return [
        {
            "json_path": path,
            "non_empty_records": 1,
            "value_types": "string",
        }
        for path in paths
    ]


def test_shadow_period_is_bounded_to_ninety_days() -> None:
    assert validate_shadow_period(1) == 1
    assert validate_shadow_period(30) == 30
    assert validate_shadow_period(MAX_SHADOW_PERIOD_DAYS) == 90

    for invalid in (0, 91, -1, True, 30.0):
        with pytest.raises(ValueError):
            validate_shadow_period(invalid)


def test_real_field_inventory_marks_load_pipeline_schema_partial() -> None:
    coverage = {
        "activities": _coverage(
            "id",
            "start_date_local",
            "type",
            "moving_time",
        ),
        "sport_settings": _coverage("types[]", "hr_zones[]"),
        "wellness": _coverage(
            "sleepQuality",
            "fatigue",
            "stress",
            "motivation",
            "restingHR",
            "hrv",
            "sleepSecs",
        ),
        "calendar": _coverage("id", "name", "start_date_local"),
    }
    streams = [
        {"stream_name": "time", "total_points": 3},
        {"stream_name": "heartrate", "total_points": 3},
        {"stream_name": "moving", "total_points": 3},
    ]

    mapping = build_mapping_report(coverage, streams)
    readiness = build_model_readiness(mapping)
    mapping_by_target = {
        row["target_field"]: row for row in mapping
    }
    readiness_by_model = {
        row["model"]: row["readiness"] for row in readiness
    }

    assert mapping_by_target["real_Z1..real_Z5"]["status"] == "derived"
    assert (
        readiness_by_model[
            "HR zoning → Q/E → 7/40/Tref/load readiness"
        ]
        == "partial"
    )
    assert (
        readiness_by_model["Wellness and integrated readiness"]
        == "partial"
    )
    assert (
        readiness_by_model["Tests, strength and weekly planning"]
        == "blocked"
    )


def test_mapping_report_contains_field_names_but_never_sample_values() -> None:
    private_value = "athlete-private-value"
    coverage = {
        "activities": [
            {
                "json_path": "moving_time",
                "non_empty_records": 1,
                "value_types": "integer",
                "sample": private_value,
            }
        ]
    }

    report = build_mapping_report(coverage)
    rendered = repr(report)

    assert "moving_time" in rendered
    assert private_value not in rendered


def test_missing_streams_keep_load_pipeline_blocked() -> None:
    coverage = {
        "activities": _coverage(
            "id", "start_date", "type", "moving_time"
        ),
        "sport_settings": _coverage("hr_zones[]"),
    }

    mapping = build_mapping_report(coverage)
    readiness = build_model_readiness(mapping)

    load = next(
        row
        for row in readiness
        if row["model"]
        == "HR zoning → Q/E → 7/40/Tref/load readiness"
    )
    assert load["readiness"] == "blocked"


def test_null_fields_and_zero_point_streams_are_not_usable_inputs() -> None:
    coverage = {
        "activities": _coverage(
            "id", "start_date", "type", "moving_time"
        ),
        "sport_settings": [
            {
                "json_path": "hr_zones[]",
                "non_empty_records": 0,
                "value_types": "null",
            }
        ],
    }
    streams = [
        {"stream_name": "time", "total_points": 0},
        {"stream_name": "heartrate", "total_points": 0},
        {"stream_name": "moving", "total_points": 0},
    ]

    mapping = build_mapping_report(coverage, streams)
    mapping_by_target = {
        row["target_field"]: row for row in mapping
    }
    readiness = build_model_readiness(mapping)
    load = next(
        row
        for row in readiness
        if row["model"]
        == "HR zoning → Q/E → 7/40/Tref/load readiness"
    )

    assert mapping_by_target["hr_zones"]["status"] == "missing"
    assert mapping_by_target["real_Z1..real_Z5"]["status"] == "missing"
    assert load["readiness"] == "blocked"
