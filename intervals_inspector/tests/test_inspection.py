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
    assert report["stream_quality"]["recording_stops"] == {
        "present": True,
        "count": 0,
    }
    rendered = repr(report)
    assert "Private activity name" not in rendered
    assert "[120, 121, 122]" not in rendered
    assert TOKEN not in rendered


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
