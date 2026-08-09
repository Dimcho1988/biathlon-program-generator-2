from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from streamlit.testing.v1 import AppTest

from intervals_inspector.intervals_client import IntervalsResponse
from intervals_inspector.real_data_source import REAL_DATA_SOURCE, load_real_history


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


class _SyntheticUIClient:
    def __init__(self, period_end: date) -> None:
        start = period_end - timedelta(days=89)
        self.activities = {
            f"ui-{offset:02d}": start + timedelta(days=offset)
            for offset in range(0, 90, 10)
        }

    def get_activities_result(self, oldest: str, newest: str) -> IntervalsResponse:
        return IntervalsResponse(
            200,
            [
                {
                    "id": activity_id,
                    "start_date_local": f"{activity_day.isoformat()}T08:00:00",
                }
                for activity_id, activity_day in self.activities.items()
            ],
        )

    def get_activity_result(
        self, activity_id: str, *, include_intervals: bool = False
    ) -> IntervalsResponse:
        assert include_intervals is False
        activity_day = self.activities[activity_id]
        return IntervalsResponse(
            200,
            {
                "id": activity_id,
                "start_date_local": f"{activity_day.isoformat()}T08:00:00",
                "type": "Run",
                "elapsed_time": 61,
                "moving_time": 61,
                "icu_recording_time": 61,
                "recording_stops": [],
            },
        )

    def get_streams_result(self, activity_id: str) -> IntervalsResponse:
        assert activity_id in self.activities
        return IntervalsResponse(
            200,
            [
                {"type": "time", "data": list(range(61))},
                {"type": "heartrate", "data": [145.0] * 61},
            ],
        )


def _values(elements: Any) -> list[str]:
    return [str(element.value) for element in elements]


def test_real_mode_never_falls_back_and_marks_unconnected_pages() -> None:
    app = AppTest.from_file(str(APP_PATH), default_timeout=180)
    app.query_params["page"] = "load"
    app.run()
    app.radio[0].set_value(REAL_DATA_SOURCE)
    app.run()

    assert not app.exception
    assert len(app.metric) == 0
    assert any(
        "Тестови данни не се използват като заместител" in value
        for value in _values(app.error)
    )
    assert "Роля" not in [element.label for element in app.selectbox]
    assert "Спортист" not in [element.label for element in app.selectbox]

    app.query_params["page"] = "team"
    app.run()
    assert not app.exception
    assert any(
        value
        == "Тази страница все още не е свързана с реалния източник на данни."
        for value in _values(app.warning)
    )


def test_load_and_recovery_render_the_same_cached_real_dataset() -> None:
    app = AppTest.from_file(str(APP_PATH), default_timeout=180)
    app.query_params["page"] = "load"
    app.run()

    profile_identifier = "synthetic-profile"
    dataset = load_real_history(
        _SyntheticUIClient(date.today()),
        profile_identifier=profile_identifier,
        session_salt=str(app.session_state["_real_history_cache_salt"]),
        parameters=app.session_state["bundle"]["parameters"],
        period_end=date.today(),
        days=90,
        loaded_at_utc=datetime(2026, 8, 8, 12, tzinfo=timezone.utc),
    )
    app.session_state["_intervals_access_token"] = True
    app.session_state["_intervals_athlete_id"] = profile_identifier
    app.session_state["_intervals_athlete_name"] = "Synthetic profile"
    app.session_state["_real_history_dataset"] = dataset
    app.radio[0].set_value(REAL_DATA_SOURCE)
    app.run()

    assert not app.exception
    assert any(
        "Източник: Реални данни от Intervals.icu" in value
        for value in _values(app.info)
    )
    assert len(app.metric) >= 5

    app.query_params["page"] = "settings"
    app.run()
    assert not app.exception
    assert any(
        "Некалибрирани начални физиологични граници" in value
        for value in _values(app.warning)
    )
    assert "_real_tref_configuration" in app.session_state

    app.query_params["page"] = "recovery"
    app.run()
    assert not app.exception
    assert app.session_state["_real_history_dataset"] is dataset
    assert any(
        "Източник: Реални данни от Intervals.icu" in value
        for value in _values(app.info)
    )
    assert any("load-only" in value for value in _values(app.warning))
