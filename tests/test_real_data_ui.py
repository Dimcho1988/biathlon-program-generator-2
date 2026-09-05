from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
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
        assert include_intervals is True
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
    assert metrics["Tref"] == (
        f"{float(dataset.load_stats.loc['Z1', 'Tref']):.1f}"
    )
    load_captions = _values(app.caption)
    assert any(
        "raw HR → effective HR → приравнено време" in value
        for value in load_captions
    )
    assert any(
        "Raw Tref = 7 × средния дневен ефективен E" in value
        for value in load_captions
    )
    assert any(
        "Текущият Tref след края на периода включва последния завършен ден"
        in value
        and "фиксираните експертни граници" in value
        for value in load_captions
    )

    dataframes = [element.value for element in app.dataframe]
    h40_table = next(
        frame
        for frame in dataframes
        if "Raw Tref (H40) преди деня" in frame.columns
        and "Предходни дни" in frame.columns
    )
    assert list(h40_table.columns) == [
        "Зона",
        "Raw Tref (H40) преди деня",
        "Tref за деня",
        "Текущ Tref след деня",
        "Tref min",
        "Tref max",
        "Приложена граница",
        "Източник за деня",
        "Предходни дни",
    ]
    expected_daily = dataset.daily_zones.loc[
        dataset.daily_zones["date"] == dataset.daily_zones["date"].max()
    ].set_index("zone")
    for row in h40_table.to_dict("records"):
        zone = str(row["Зона"])
        assert float(row["Tref за деня"]) == pytest.approx(
            float(expected_daily.loc[zone, "tref_effective"])
        )
        assert float(row["Текущ Tref след деня"]) == pytest.approx(
            float(dataset.load_stats.loc[zone, "Tref"])
        )
        assert float(row["Tref min"]) <= float(row["Tref за деня"])
        assert float(row["Tref за деня"]) <= float(row["Tref max"])
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
        "Raw Tref (H40)",
        "Tref",
        "Tref min",
        "Tref max",
        "Приложена граница",
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
        "Tref се изчислява от реалния E" in value
        and "фиксираните експертни граници" in value
        and "Линейният pp/bpm параметър" in value
        for value in _values(app.warning)
    )
    assert "_real_tref_configuration" in app.session_state
    settings_markdown = _values(app.markdown)
    for value in (
        "180–300 приравнени мин",
        "90–180 приравнени мин",
        "40–70 приравнени мин",
        "10–20 приравнени мин",
    ):
        assert value in settings_markdown
    assert (
        settings_markdown.count(
            "Реален E за до 40 дни · cold start = max"
        )
        == 5
    )
    assert "**Tref граници**" in settings_markdown
    assert "**pp/bpm**" in settings_markdown
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
    assert "фиксирана начална експертна настройка" not in visible_settings_text
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
