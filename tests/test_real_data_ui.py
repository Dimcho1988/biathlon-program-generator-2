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
        self,
        activity_id: str,
        *,
        include_intervals: bool = False,
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


def test_load_settings_and_recovery_share_the_new_real_model_contract() -> None:
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
    metrics = {element.label: str(element.value) for element in app.metric}
    assert metrics["Tref"] == "300.0"
    load_captions = _values(app.caption)
    assert any(
        "raw HR → effective HR → приравнено време" in value
        for value in load_captions
    )
    assert any(
        "H40 използва само завършени календарни дни" in value
        for value in load_captions
    )
    assert any(
        "Tref е фиксирана начална експертна настройка" in value
        and "7/40" in value
        for value in load_captions
    )

    dataframes = [element.value for element in app.dataframe]
    h40_table = next(
        frame
        for frame in dataframes
        if "H40 (диагностично)" in frame.columns
        and "Завършени дни" in frame.columns
    )
    assert list(h40_table.columns) == [
        "Зона",
        "H40 (диагностично)",
        "Tref",
        "Източник",
        "Завършени дни",
    ]
    assert dict(zip(h40_table["Зона"], h40_table["Tref"])) == {
        "Z1": 300.0,
        "Z2": 180.0,
        "Z3": 70.0,
        "Z4": 20.0,
        "Z5": 20.0,
    }
    activity_table = next(
        frame
        for frame in dataframes
        if "Приравнено време (мин)" in frame.columns
    )
    assert {
        "Реално време (мин)",
        "Приравнено време (мин)",
        "Среден HR (времево претеглен)",
        "Средна стойност на минутата (%)",
        "% от Tref",
        "H40 (диагностично)",
        "Tref",
    } <= set(activity_table.columns)
    assert not any(
        "Qref" in str(column) or str(column) in {"Q", "Q_z"}
        for frame in dataframes
        for column in frame.columns
    )

    app.query_params["page"] = "settings"
    app.run()
    assert not app.exception
    assert any(
        "Tref е фиксирана начална експертна настройка" in value
        and "Линейният pp/bpm параметър" in value
        for value in _values(app.warning)
    )
    assert "_real_tref_configuration" in app.session_state
    settings_markdown = _values(app.markdown)
    for value in (
        "300 приравнени мин",
        "180 приравнени мин",
        "70 приравнени мин",
        "20 приравнени мин",
    ):
        assert value in settings_markdown
    assert settings_markdown.count("Начална експертна настройка") == 5
    assert "**Tref**" in settings_markdown
    assert "**pp/bpm**" in settings_markdown
    assert not any(
        label in settings_markdown
        for label in ("Tref минимум", "Tref максимум", "Tref ограничение")
    )
    slope_inputs = {
        element.label: float(element.value)
        for element in app.number_input
        if element.label.endswith("pp/bpm")
    }
    assert slope_inputs == {
        "Z1 pp/bpm": 3.0,
        "Z2 pp/bpm": 3.0,
        "Z3 pp/bpm": 3.0,
        "Z4 pp/bpm": 3.0,
        "Z5 pp/bpm": 3.0,
    }
    visible_settings_text = "\n".join(
        _values(app.warning) + _values(app.caption) + settings_markdown
    )
    assert "Qref" not in visible_settings_text

    app.query_params["page"] = "recovery"
    app.run()
    assert not app.exception
    assert app.session_state["_real_history_dataset"] is dataset
    assert any(
        "Източник: Реални данни от Intervals.icu" in value
        for value in _values(app.info)
    )
    assert any("load-only" in value for value in _values(app.warning))
