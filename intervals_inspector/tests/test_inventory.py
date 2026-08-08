from __future__ import annotations

import csv
import io
import json

import pytest

from intervals_inspector.inventory import (
    build_field_coverage,
    export_inventory_csv,
    export_inventory_json,
    redact_sensitive_data,
    summarize_streams,
)


def _by_path(rows: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {str(row["json_path"]): row for row in rows}


def _by_stream(rows: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {str(row["stream_name"]): row for row in rows}


def test_recursive_redaction_omits_credentials_location_and_profile_values():
    original = {
        "access_token": "token-that-must-not-leak",
        "icu_api_key": "intervals-api-key-must-not-leak",
        "safe": "kept",
        "callback": "https://example.test/?code=short-lived-code",
        "nested": [
            {
                "clientSecret": "client-secret-value",
                "route": {"polyline": "encoded-route"},
                "location": {"lat": 42.1, "lng": 23.2},
                "description": "private training description",
                "profile": {
                    "id": "athlete-123",
                    "name": "Private Athlete",
                    "email": "athlete@example.test",
                    "sport": "cycling",
                },
                "metrics": {"load": 55},
            }
        ],
    }

    cleaned = redact_sensitive_data(original)
    rendered = json.dumps(cleaned)

    assert cleaned["safe"] == "kept"
    assert cleaned["nested"][0]["profile"] == {"sport": "cycling"}
    assert cleaned["nested"][0]["metrics"] == {"load": 55}
    for secret in (
        "token-that-must-not-leak",
        "intervals-api-key-must-not-leak",
        "short-lived-code",
        "client-secret-value",
        "encoded-route",
        "Private Athlete",
        "athlete@example.test",
        "42.1",
        "23.2",
    ):
        assert secret not in rendered
    assert original["access_token"] == "token-that-must-not-leak"


def test_field_coverage_handles_nested_and_list_fields_and_custom_flags():
    records = [
        {
            "id": 1,
            "metrics": {"heart_rate": 151, "custom_alpha": 7},
            "laps": [{"pace": 4.2}, {"pace": None}],
            "empty_text": "",
            "notes": "must be omitted",
        },
        {
            "id": 2,
            "metrics": {"heart_rate": None, "custom_beta": "present"},
            "laps": [],
        },
    ]
    standard_fields = {
        "id",
        "metrics",
        "metrics.heart_rate",
        "laps",
        "laps[]",
        "laps[].pace",
        "empty_text",
    }

    rows = build_field_coverage(
        records,
        "https://intervals.icu/api/v1/athlete/i12345/activities?token=bad",
        standard_fields,
    )
    by_path = _by_path(rows)

    assert by_path["metrics.heart_rate"] == {
        "json_path": "metrics.heart_rate",
        "source_endpoint": "/api/v1/athlete/{athlete_id}/activities",
        "value_types": "null, integer",
        "records_present": 2,
        "non_empty_records": 1,
        "coverage_percent": 50.0,
        "classification": "standard",
    }
    assert by_path["laps[].pace"]["records_present"] == 1
    assert by_path["laps[].pace"]["non_empty_records"] == 1
    assert by_path["laps[].pace"]["coverage_percent"] == 50.0
    assert by_path["metrics.custom_alpha"]["classification"] == "unknown/custom"
    assert by_path["metrics.custom_beta"]["classification"] == "unknown/custom"
    assert "notes" not in by_path
    assert all("sample" not in row for row in rows)


def test_unsafe_or_pii_bearing_json_keys_never_become_inventory_paths():
    records = [
        {
            "safe_field": 1,
            "nested": {
                "safe-custom": 2,
                "start_latlng": [42.1, 23.2],
                "end_latlon": [42.2, 23.3],
                "position_lnglat": [23.4, 42.3],
                "athlete@example.test": "private email key",
                "Private Athlete Name": "private label key",
                "line\nbreak": "control text key",
                "=HYPERLINK": "formula key",
            },
        }
    ]

    cleaned = redact_sensitive_data(records)
    coverage = build_field_coverage(
        records,
        "/api/v1/athlete/{athlete_id}/activities",
    )
    json_report = export_inventory_json(coverage)
    csv_report = export_inventory_csv(coverage)
    paths = {row["json_path"] for row in coverage}

    assert paths == {"nested", "nested.safe-custom", "safe_field"}
    rendered = json.dumps(cleaned)
    for unsafe_text in (
        "athlete@example.test",
        "Private Athlete Name",
        "line\\nbreak",
        "=HYPERLINK",
        "private email key",
        "private label key",
        "control text key",
        "formula key",
    ):
        assert unsafe_text not in rendered
        assert unsafe_text not in json_report
        assert unsafe_text not in csv_report


def test_stream_summary_is_limited_and_estimates_only_reliable_frequency():
    activities = [
        {
            "activity_id": "private-1",
            "streams": {
                "time": [0, 1, 2, 3],
                "heartrate": {
                    "data": [120, 122, 124, 126],
                    "value_type": "integer",
                    "unit": "bpm",
                },
                "watts": {"data": [180, 190, 195, 200], "unit": "W"},
                "gps": [42.1, 42.2, 42.3, 42.4],
            },
        },
        {
            "activity_id": "private-2",
            "streams": {
                "time": [0, 1, 2],
                "heartrate": {
                    "data": [118, 121, 123],
                    "value_type": "integer",
                    "unit": "bpm",
                },
            },
        },
        {
            "activity_id": "must-be-limited-out",
            "streams": {
                "time": [0, 0.5, 1.0],
                "heartrate": {"data": [100, 101, 102], "unit": "bpm"},
            },
        },
    ]

    rows = summarize_streams(activities, max_activities=2)
    by_stream = _by_stream(rows)

    assert set(by_stream) == {"gps", "heartrate", "time", "watts"}
    assert by_stream["heartrate"] == {
        "stream_name": "heartrate",
        "value_type": "integer",
        "unit": "bpm",
        "activity_count": 2,
        "total_points": 7,
        "estimated_frequency_hz": 1.0,
    }
    assert by_stream["watts"]["activity_count"] == 1
    assert by_stream["watts"]["total_points"] == 4
    assert by_stream["watts"]["estimated_frequency_hz"] == 1.0
    assert "activity_id" not in json.dumps(rows)


def test_gps_stream_presence_is_reported_without_coordinate_values():
    latitude = 42.123456
    longitude = 23.654321
    rows = summarize_streams(
        [
            {
                "streams": [
                    {
                        "type": "latlng",
                        "data": [latitude, latitude + 0.001],
                        "data2": [longitude, longitude + 0.001],
                    }
                ]
            }
        ]
    )

    assert rows == [
        {
            "stream_name": "latlng",
            "value_type": None,
            "unit": None,
            "activity_count": 1,
            "total_points": 2,
            "estimated_frequency_hz": None,
        }
    ]
    rendered_rows = repr(rows)
    json_report = export_inventory_json([], rows)
    assert "latlng" in rendered_rows
    assert "latlng" in json_report
    assert str(latitude) not in rendered_rows
    assert str(longitude) not in rendered_rows
    assert str(latitude) not in json_report
    assert str(longitude) not in json_report


def test_frequency_is_omitted_for_irregular_or_misaligned_samples():
    rows = summarize_streams(
        [
            {
                "streams": {
                    "time": [0, 1, 4, 5],
                    "heartrate": {"data": [120, 121, 122, 123]},
                    "cadence": {"data": [80, 81]},
                }
            }
        ]
    )

    by_stream = _by_stream(rows)
    assert by_stream["time"]["estimated_frequency_hz"] is None
    assert by_stream["heartrate"]["estimated_frequency_hz"] is None
    assert by_stream["cadence"]["estimated_frequency_hz"] is None


@pytest.mark.parametrize("exporter", [export_inventory_json, export_inventory_csv])
def test_exports_allow_only_deidentified_columns(exporter):
    coverage = [
        {
            "json_path": "metrics.heart_rate",
            "source_endpoint": (
                "https://intervals.icu/api/v1/athlete/i9876/activities"
                "?access_token=export-token-secret"
            ),
            "value_types": "integer",
            "records_present": 2,
            "non_empty_records": 2,
            "coverage_percent": 100,
            "classification": "standard",
            "sample_value": "athlete@example.test",
            "access_token": "top-secret-token",
        },
        {
            "json_path": "profile.email",
            "source_endpoint": "/api/v1/profile",
            "value_types": "string",
            "records_present": 1,
            "non_empty_records": 1,
            "coverage_percent": 100,
            "classification": "standard",
            "sample_value": "athlete@example.test",
        },
    ]
    streams = [
        {
            "stream_name": "heartrate",
            "value_type": "integer",
            "unit": "bpm",
            "activity_count": 2,
            "total_points": 7,
            "estimated_frequency_hz": 1.0,
            "points": [120, 121, 122],
            "authorization_code": "short-code",
        },
        {
            "stream_name": "polyline",
            "value_type": "string",
            "unit": None,
            "activity_count": 1,
            "total_points": 1,
            "estimated_frequency_hz": None,
            "points": ["encoded-gps-route"],
        },
    ]

    report = exporter(coverage, streams)

    for sensitive_value in (
        "export-token-secret",
        "top-secret-token",
        "athlete@example.test",
        "short-code",
        "encoded-gps-route",
        "profile.email",
        "polyline",
        "120",
        "121",
        "122",
        "i9876",
    ):
        assert sensitive_value not in report
    assert "metrics.heart_rate" in report
    assert "heartrate" in report
    assert "{athlete_id}" in report


def test_json_and_csv_exports_have_stable_safe_schemas():
    coverage = build_field_coverage(
        [{"metrics": {"heart_rate": 150}}],
        "/api/v1/athlete/{athlete_id}/activities",
        {"metrics", "metrics.heart_rate"},
    )
    streams = summarize_streams(
        [{"streams": {"time": [0, 1, 2], "heartrate": [120, 121, 122]}}]
    )

    json_report = json.loads(export_inventory_json(coverage, streams))
    assert set(json_report) == {
        "endpoint_checks",
        "field_coverage",
        "mapping_report",
        "model_readiness",
        "streams",
    }
    assert json_report["endpoint_checks"] == []
    assert json_report["mapping_report"] == []
    assert json_report["model_readiness"] == []
    assert set(json_report["field_coverage"][0]) == {
        "json_path",
        "source_endpoint",
        "value_types",
        "records_present",
        "non_empty_records",
        "coverage_percent",
        "classification",
    }
    assert set(json_report["streams"][0]) == {
        "stream_name",
        "value_type",
        "unit",
        "activity_count",
        "total_points",
        "estimated_frequency_hz",
    }

    csv_rows = list(csv.DictReader(io.StringIO(export_inventory_csv(
        coverage, streams
    ))))
    assert {row["report_section"] for row in csv_rows} == {
        "field_coverage",
        "streams",
    }


def test_endpoint_mapping_and_model_exports_are_metadata_only():
    sensitive = "must-never-appear"
    endpoint_checks = [
        {
            "category": "Activities",
            "endpoint": "/api/v1/activity/{activity_id}",
            "http_status": 403,
            "available": False,
            "record_count": 0,
            "field_names": ["id", "moving_time", "profile.email"],
            "safe_error": (
                "Denied code=" + sensitive + " state=" + sensitive
            ),
        }
    ]
    mapping = [
        {
            "target_field": "moving_min",
            "status": "derived",
            "matched_source_fields": "activities:moving_time",
            "missing_source_fields": "",
            "model_consumers": "activity_metadata",
            "note": "Seconds divided by 60.",
            "sample_value": sensitive,
        }
    ]
    models = [
        {
            "model": "Load model",
            "readiness": "partial",
            "missing_or_limit": "Needs a selected HR stream.",
            "token": sensitive,
        }
    ]

    json_report = export_inventory_json(
        [],
        [],
        endpoint_checks,
        mapping,
        models,
    )
    csv_report = export_inventory_csv(
        [],
        [],
        endpoint_checks,
        mapping,
        models,
    )

    assert sensitive not in json_report
    assert sensitive not in csv_report
    assert "profile.email" not in json_report
    assert "profile.email" not in csv_report
    assert "moving_min" in json_report
    assert "Load model" in json_report


def test_csv_neutralizes_formula_prefixes_in_all_projected_text_cells():
    coverage = [
        {
            "json_path": "metrics.heart_rate",
            "source_endpoint": "-endpoint",
            "value_types": "+integer",
            "records_present": 1,
            "non_empty_records": 1,
            "coverage_percent": 100,
            "classification": "standard",
        }
    ]
    streams = [
        {
            "stream_name": "-heartrate",
            "value_type": "+integer",
            "unit": "-bpm",
            "activity_count": 1,
            "total_points": 3,
            "estimated_frequency_hz": 1.0,
        }
    ]

    rows = list(csv.DictReader(io.StringIO(export_inventory_csv(
        coverage, streams
    ))))

    assert rows[0]["source_endpoint"] == "'-endpoint"
    assert rows[0]["value_types"] == "'+integer"
    assert rows[1]["stream_name"] == "'-heartrate"
    assert rows[1]["value_type"] == "'+integer"
    assert rows[1]["unit"] == "'-bpm"
    for row in rows:
        for value in row.values():
            if not value:
                continue
            stripped = value.lstrip()
            assert stripped[0] not in "=+-@" or value.startswith("'")
