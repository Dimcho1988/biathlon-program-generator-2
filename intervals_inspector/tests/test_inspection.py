from __future__ import annotations

from typing import Any

import pytest

from intervals_inspector import app as inspector_app
from intervals_inspector.intervals_client import (
    IntervalsAPIError,
    IntervalsResponse,
)


TOKEN = "inspection-token-must-not-leak"


class FakeClient:
    last_instance: "FakeClient | None" = None

    def __init__(self, access_token: str, athlete_id: str) -> None:
        assert access_token == TOKEN
        assert athlete_id == "athlete-123"
        self.activity_range: tuple[str, str] | None = None
        self.wellness_range: tuple[str, str] | None = None
        self.event_ranges: list[tuple[str, str, str | None]] = []
        FakeClient.last_instance = self

    def get_athlete_result(self) -> IntervalsResponse:
        return IntervalsResponse(
            200,
            {
                "id": "athlete-123",
                "name": "Private Athlete",
                "email": "private@example.test",
                "timezone": "Europe/Sofia",
            },
        )

    def get_sport_settings_result(self) -> IntervalsResponse:
        return IntervalsResponse(
            200,
            [{"types": ["Ride"], "hr_zones": [120, 140, 155]}],
        )

    def get_activities_result(
        self, oldest: str, newest: str
    ) -> IntervalsResponse:
        self.activity_range = (oldest, newest)
        return IntervalsResponse(
            200,
            [
                {
                    "id": "i123",
                    "name": "Private activity name",
                    "start_date_local": "2026-07-29T08:00:00",
                    "type": "Ride",
                    "moving_time": 3600,
                }
            ],
        )

    def get_wellness_result(
        self, oldest: str, newest: str
    ) -> IntervalsResponse:
        self.wellness_range = (oldest, newest)
        return IntervalsResponse(
            200,
            [{"id": newest, "fatigue": 2, "comments": "Private note"}],
        )

    def get_events_result(
        self,
        oldest: str,
        newest: str,
        *,
        category: str | None = None,
    ) -> IntervalsResponse:
        assert category in {None, "WORKOUT"}
        self.event_ranges.append((oldest, newest, category))
        return IntervalsResponse(200, [])

    def get_activity_result(
        self, activity_id: str, *, include_intervals: bool = False
    ) -> IntervalsResponse:
        assert activity_id == "i123"
        assert include_intervals is False
        return IntervalsResponse(
            200,
            {
                "id": "i123",
                "name": "Private activity name",
                "stream_types": ["time", "heartrate"],
                "elapsed_time": 2,
                "moving_time": 2,
                "icu_recording_time": 2,
                "recording_stops": [],
                "icu_hr_zones": [119, 140, 180],
                "icu_hr_zone_times": [0, 2, 0],
            },
        )

    def get_streams_result(self, activity_id: str) -> IntervalsResponse:
        assert activity_id == "i123"
        return IntervalsResponse(
            200,
            [
                {"type": "time", "data": [0, 1, 2]},
                {"type": "heartrate", "data": [120, 121, 122]},
            ],
        )


def _session(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    session: dict[str, Any] = {
        inspector_app.SESSION_TOKEN: TOKEN,
        inspector_app.SESSION_ATHLETE_ID: "athlete-123",
    }
    monkeypatch.setattr(
        inspector_app.st, "session_state", session, raising=False
    )
    monkeypatch.setattr(inspector_app, "IntervalsClient", FakeClient)
    return session


def test_overview_inspection_is_bounded_and_value_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _session(monkeypatch)

    report, choices = inspector_app._run_inspection(7)

    assert FakeClient.last_instance is not None
    oldest, newest = FakeClient.last_instance.activity_range or ("", "")
    assert oldest and newest
    assert (
        inspector_app.date.fromisoformat(newest)
        - inspector_app.date.fromisoformat(oldest)
    ).days == 6
    assert len(report["endpoint_checks"]) == 6
    assert all(
        check["http_status"] == 200
        for check in report["endpoint_checks"]
    )
    assert choices == [
        {
            "activity_id": "i123",
            "label": "2026-07-29 08:00 · Ride",
        }
    ]

    rendered = repr(report)
    assert TOKEN not in rendered
    assert "Private Athlete" not in rendered
    assert "Private activity name" not in rendered
    assert "private@example.test" not in rendered
    assert "Private note" not in rendered


@pytest.mark.parametrize("period_days", [0, 91, True])
def test_inspection_period_rejects_unbounded_values(
    monkeypatch: pytest.MonkeyPatch,
    period_days: Any,
) -> None:
    _session(monkeypatch)

    with pytest.raises(ValueError):
        inspector_app._run_inspection(period_days)


def test_ninety_days_expands_only_activity_list_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _session(monkeypatch)

    inspector_app._run_inspection(90)

    client = FakeClient.last_instance
    assert client is not None
    activity_oldest, newest = client.activity_range or ("", "")
    supporting_oldest, supporting_newest = client.wellness_range or ("", "")
    assert (
        inspector_app.date.fromisoformat(newest)
        - inspector_app.date.fromisoformat(activity_oldest)
    ).days == 89
    assert supporting_newest == newest
    assert (
        inspector_app.date.fromisoformat(supporting_newest)
        - inspector_app.date.fromisoformat(supporting_oldest)
    ).days == 29
    assert len(client.event_ranges) == 2
    assert all(
        oldest == supporting_oldest and event_newest == newest
        for oldest, event_newest, _category in client.event_ranges
    )


def test_selected_activity_detail_and_stream_report_excludes_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _session(monkeypatch)

    report = inspector_app._run_activity_inspection("i123")

    assert [row["http_status"] for row in report["endpoint_checks"]] == [
        200,
        200,
    ]
    stream_names = {
        row["stream_name"] for row in report["streams"]
    }
    assert stream_names == {"heartrate", "time"}
    assert report["stream_quality"]["timing"]["estimated_frequency_hz"] == 1.0
    recording_stops = report["stream_quality"]["recording_stops"]
    assert recording_stops["present"] is True
    assert recording_stops["count"] == 0
    assert recording_stops["structure_type"] == "integer_marker_list"
    assert recording_stops["matched_gap_count"] == 0
    rendered = repr(report)
    assert "Private activity name" not in rendered
    assert "[120, 121, 122]" not in rendered
    assert TOKEN not in rendered


@pytest.mark.parametrize("include_1hz_preview", [False, True])
def test_selected_activity_normalizer_keeps_only_aggregate_summary(
    monkeypatch: pytest.MonkeyPatch,
    include_1hz_preview: bool,
) -> None:
    _session(monkeypatch)

    summary = inspector_app._run_activity_normalizer(
        "i123",
        include_1hz_preview=include_1hz_preview,
    )

    assert summary["fast_path_used"] is True
    assert summary["input_point_count"] == 3
    assert summary["active_duration_sec"] == 2
    assert summary["materialize_1hz"]["requested"] is include_1hz_preview
    assert summary["materialize_1hz"]["point_count"] == (
        3 if include_1hz_preview else 0
    )
    zone_analysis = summary["zone_analysis"]
    assert zone_analysis["available"] is True
    assert zone_analysis["active_duration_sec"] == 2
    assert zone_analysis["classified_hr_sec"] == 2
    assert [row["seconds"] for row in zone_analysis["zones"]] == [
        0,
        2,
        0,
    ]
    assert zone_analysis["intervals_reference_available"] is True
    onflows = summary["onflows_load_analysis"]
    assert onflows["algorithm_version"] == (
        "onflows-intrazone-load-interval-aware-v1"
    )
    assert onflows["profile_schema_version"] == "onflows-zone-profile-v1"
    assert len(onflows["zones"]) == 5
    assert onflows["active_duration_sec"] == 2
    assert onflows["classified_hr_sec"] == 2
    assert onflows["total_weighted_sec"] >= onflows["total_real_sec"]
    assert len(summary["onflows_zone_profile"]["fingerprint"]) == 64
    rendered = repr(summary)
    assert TOKEN not in rendered
    assert "i123" not in rendered
    assert "Private activity name" not in rendered
    assert "[120, 121, 122]" not in rendered


def test_zone_path_does_not_materialize_1hz(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _session(monkeypatch)
    from intervals_inspector import pipeline

    monkeypatch.setattr(
        pipeline,
        "materialize_1hz",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("zone path must not materialize 1 Hz")
        ),
    )

    summary = inspector_app._run_activity_normalizer(
        "i123",
        include_1hz_preview=False,
    )

    assert summary["materialize_1hz"]["requested"] is False
    assert summary["zone_analysis"]["classified_hr_sec"] == 2
    assert summary["onflows_load_analysis"]["classified_hr_sec"] == 2
    assert summary["onflows_load_analysis"]["total_weighted_sec"] > 0


def test_endpoint_failure_exposes_only_safe_status_and_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingClient(FakeClient):
        def get_wellness_result(
            self, oldest: str, newest: str
        ) -> IntervalsResponse:
            raise IntervalsAPIError(
                "Липсва необходимо разрешение за тези данни (403).",
                status_code=403,
            )

    _session(monkeypatch)
    monkeypatch.setattr(inspector_app, "IntervalsClient", FailingClient)

    report, _choices = inspector_app._run_inspection(7)
    wellness = next(
        row
        for row in report["endpoint_checks"]
        if row["category"] == "Wellness"
    )

    assert wellness["http_status"] == 403
    assert wellness["available"] is False
    assert wellness["record_count"] == 0
    assert wellness["field_names"] == []
    assert TOKEN not in repr(wellness)


def test_activity_choices_disambiguate_same_time_and_sport() -> None:
    choices = inspector_app._activity_choices(
        [
            {
                "id": "i1",
                "start_date_local": "2026-07-29T08:00:00",
                "type": "Ride",
            },
            {
                "id": "i2",
                "start_date_local": "2026-07-29T08:00:30",
                "type": "Ride",
            },
        ]
    )

    assert choices == [
        {
            "activity_id": "i1",
            "label": "2026-07-29 08:00 · Ride",
        },
        {
            "activity_id": "i2",
            "label": "2026-07-29 08:00 · Ride · #2",
        },
    ]
