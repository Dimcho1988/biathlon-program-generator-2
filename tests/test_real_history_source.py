from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from biathlon.demo_data import generate_demo_bundle
from intervals_inspector.intervals_client import IntervalsResponse
from intervals_inspector.real_data_source import (
    DEMO_DATA_SOURCE,
    REAL_DATA_SOURCE,
    build_history_cache_key,
    build_real_load_view,
    build_real_recovery_view,
    load_real_history,
    recovery_parameter_fingerprint,
    resolve_real_dataset,
    validate_data_source,
)
from intervals_inspector.shadow_model import (
    configuration_with_overrides,
    default_shadow_configuration,
)


def _activity_detail(activity_id: str, activity_day: date) -> dict[str, Any]:
    return {
        "id": activity_id,
        "name": "Private real name",
        "start_date_local": f"{activity_day.isoformat()}T08:00:00",
        "type": "Run",
        "elapsed_time": 60,
        "moving_time": 60,
        "icu_recording_time": 60,
        "recording_stops": [],
        "latlng": [[42.0, 23.0]],
    }


class SyntheticHistoryClient:
    def __init__(self, specs: dict[str, dict[str, Any]]) -> None:
        self.specs = specs
        self.activity_ranges: list[tuple[str, str]] = []

    def get_activities_result(self, oldest: str, newest: str) -> IntervalsResponse:
        self.activity_ranges.append((oldest, newest))
        start = date.fromisoformat(oldest)
        end = date.fromisoformat(newest)
        return IntervalsResponse(
            200,
            [
                {
                    "id": activity_id,
                    "start_date_local": f"{spec['date'].isoformat()}T08:00:00",
                    "name": "Must not survive",
                }
                for activity_id, spec in sorted(self.specs.items())
                if start <= spec["date"] <= end
            ],
        )

    def get_activity_result(
        self, activity_id: str, *, include_intervals: bool = False
    ) -> IntervalsResponse:
        assert include_intervals is False
        detail = _activity_detail(activity_id, self.specs[activity_id]["date"])
        detail["recording_stops"] = self.specs[activity_id].get(
            "recording_stops", []
        )
        return IntervalsResponse(
            200,
            detail,
        )

    def get_streams_result(self, activity_id: str) -> IntervalsResponse:
        spec = self.specs[activity_id]
        offsets = spec.get("offsets", list(range(61)))
        hr = spec.get("hr", [145.0] * len(offsets))
        return IntervalsResponse(
            200,
            [
                {"type": "time", "data": offsets},
                {"type": "heartrate", "data": hr},
                {"type": "latlng", "data": [[42.0, 23.0]] * len(offsets)},
            ],
        )


def _specs(start: date, days: int) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for offset in range(0, days, 3):
        activity_day = start + timedelta(days=offset)
        result[f"real-{offset:03d}"] = {"date": activity_day}
    shared_day = start + timedelta(days=69)
    result["same-day-second"] = {"date": shared_day, "hr": [155.0] * 61}
    result["partial-hr"] = {
        "date": start + timedelta(days=75),
        "hr": [145.0] * 31 + [None] * 30,
    }
    result["missing-hr"] = {
        "date": start + timedelta(days=80),
        "hr": [None] * 61,
    }
    result["recording-gap"] = {
        "date": start + timedelta(days=85),
        "offsets": [0, 1, 20, 21],
        "hr": [145.0] * 4,
        "recording_stops": [{"start": 1, "end": 20}],
    }
    return result


def _parameters() -> dict[str, Any]:
    return generate_demo_bundle()["parameters"]


def test_data_source_selection_is_explicit_and_never_falls_back() -> None:
    assert validate_data_source(DEMO_DATA_SOURCE) == DEMO_DATA_SOURCE
    assert validate_data_source(REAL_DATA_SOURCE) == REAL_DATA_SOURCE
    with pytest.raises(ValueError, match="unknown data source"):
        validate_data_source("unexpected")


def test_cache_key_accounts_for_profile_period_and_models_without_secrets() -> None:
    parameters = _parameters()
    configuration = default_shadow_configuration()
    key = build_history_cache_key(
        profile_identifier="athlete-private-123",
        session_salt="session-local-random-salt",
        period_start=date(2026, 5, 11),
        period_end=date(2026, 8, 8),
        configuration=configuration,
        parameter_fingerprint=recovery_parameter_fingerprint(parameters),
    )

    assert len(key) == 64
    assert "athlete-private-123" not in key
    assert "session-local-random-salt" not in key
    for forbidden in ("token", "secret", "password", "oauth"):
        assert forbidden not in key.lower()

    changed_configuration = configuration_with_overrides(
        {
            "parameter.Z2.tref_min": 91.0
        }
    )
    changed_key = build_history_cache_key(
        profile_identifier="athlete-private-123",
        session_salt="session-local-random-salt",
        period_start=date(2026, 5, 11),
        period_end=date(2026, 8, 8),
        configuration=changed_configuration,
        parameter_fingerprint=recovery_parameter_fingerprint(parameters),
    )
    assert changed_key != key


def test_ninety_day_history_builds_one_shared_load_and_recovery_dataset() -> None:
    end = date(2026, 8, 8)
    start = end - timedelta(days=89)
    dataset = load_real_history(
        SyntheticHistoryClient(_specs(start, 90)),
        profile_identifier="athlete-123",
        session_salt="session-salt",
        parameters=_parameters(),
        period_end=end,
        days=90,
        loaded_at_utc=datetime(2026, 8, 8, 12, tzinfo=timezone.utc),
    )

    assert dataset.period_start == start.isoformat()
    assert dataset.period_end == end.isoformat()
    assert len(dataset.daily_loads) == 90
    assert not any(column.endswith("_STR") for column in dataset.daily_loads)
    assert set(dataset.load_stats.index) == {"Z1", "Z2", "Z3", "Z4", "Z5"}
    assert set(dataset.load_readiness.index) == {"Z1", "Z2", "Z3", "Z4", "Z5"}
    assert len(dataset.daily_zones) == 90 * 5
    assert dataset.processed_activities == len(_specs(start, 90))
    assert dataset.limited_activities == 1
    assert dataset.excluded_activities == 1
    assert set(dataset.activities["quality_status"]) >= {
        "valid",
        "limited",
        "excluded",
    }
    assert not dataset.readiness_history.empty
    assert not dataset.load_readiness.empty
    assert {
            "T_z",
            "Q_z",
            "Qref_z",
            "direct_ratio",
        "cascade",
        "spillover",
        "E_z",
        "tref_raw",
        "tref_effective",
    } <= set(dataset.activity_zones)
    assert float(dataset.activity_zones["T_z"].sum()) > 0.0
    assert float(dataset.activity_zones["Q_z"].sum()) > 0.0
    assert float(dataset.activity_zones["cascade"].sum()) > 0.0
    assert float(dataset.activity_zones["spillover"].sum()) >= 0.0
    assert float(dataset.activity_zones["E_z"].sum()) > 0.0
    for component in ("Z2", "Z3", "Z4"):
        current_stats = dataset.load_stats.loc[component]
        rolling_stats = dataset.rolling_load.loc[
            dataset.rolling_load["component"] == component
        ].iloc[-1]
        assert current_stats["E7_daily"] == pytest.approx(
            rolling_stats["E7_daily"]
        )
        assert current_stats["E40_daily"] == pytest.approx(
            rolling_stats["E40_daily"]
        )
        assert current_stats["index_7_40"] == pytest.approx(
            rolling_stats["index_7_40"]
        )
    assert build_real_load_view(dataset)["daily_zones"] is dataset.daily_zones
    assert (
        build_real_recovery_view(dataset)["readiness_history"]
        is dataset.readiness_history
    )


def test_multiple_activities_aggregate_after_individual_modeling() -> None:
    end = date(2026, 8, 8)
    start = end - timedelta(days=89)
    dataset = load_real_history(
        SyntheticHistoryClient(_specs(start, 90)),
        profile_identifier="athlete-123",
        session_salt="session-salt",
        parameters=_parameters(),
        period_end=end,
        days=90,
    )
    shared_day = pd.Timestamp(start + timedelta(days=69))
    activity_refs = dataset.activities.loc[
        dataset.activities["date"] == shared_day, "activity_ref"
    ]
    assert len(activity_refs) == 2
    individual = dataset.activity_zones.loc[
        (dataset.activity_zones["date"] == shared_day)
        & (dataset.activity_zones["zone"] == "Z3"),
        "E_z",
    ].sum()
    daily = dataset.daily_zones.loc[
        (dataset.daily_zones["date"] == shared_day)
        & (dataset.daily_zones["zone"] == "Z3"),
        "E_z",
    ].iloc[0]
    assert daily == pytest.approx(individual)


def test_tref_uses_only_previous_calendar_days_and_excludes_current_day() -> None:
    end = date(2026, 8, 8)
    start = end - timedelta(days=89)
    dataset = load_real_history(
        SyntheticHistoryClient(_specs(start, 90)),
        profile_identifier="athlete-123",
        session_salt="session-salt",
        parameters=_parameters(),
        period_end=end,
        days=90,
    )
    current = pd.Timestamp(start + timedelta(days=69))
    zone_history = dataset.daily_zones.loc[
        (dataset.daily_zones["zone"] == "Z2")
        & (dataset.daily_zones["date"] < current)
        & (dataset.daily_zones["date"] >= current - pd.Timedelta(days=40))
    ].sort_values("date")
    expected = 7.0 * float(zone_history["Qref_z"].mean())
    actual = dataset.daily_zones.loc[
        (dataset.daily_zones["date"] == current)
        & (dataset.daily_zones["zone"] == "Z2"),
        "tref_raw",
    ].iloc[0]
    assert len(zone_history) == 40
    assert actual == pytest.approx(expected)
    same_day_trefs = dataset.activity_zones.loc[
        (dataset.activity_zones["date"] == current)
        & (dataset.activity_zones["zone"] == "Z2"),
        "tref_raw",
    ]
    assert len(same_day_trefs) == 2
    assert all(value == pytest.approx(expected) for value in same_day_trefs)


def test_future_activities_do_not_change_past_results() -> None:
    shared_start = date(2026, 5, 1)
    full_end = date(2026, 8, 8)
    past_end = date(2026, 7, 29)
    specs = _specs(shared_start, 100)
    parameters = _parameters()
    full = load_real_history(
        SyntheticHistoryClient(specs),
        profile_identifier="athlete-123",
        session_salt="session-salt",
        parameters=parameters,
        period_end=full_end,
        days=100,
        loaded_at_utc=datetime(2026, 8, 8, tzinfo=timezone.utc),
    )
    past = load_real_history(
        SyntheticHistoryClient(specs),
        profile_identifier="athlete-123",
        session_salt="session-salt",
        parameters=parameters,
        period_end=past_end,
        days=90,
        loaded_at_utc=datetime(2026, 8, 8, tzinfo=timezone.utc),
    )
    overlap = full.daily_zones.loc[
        full.daily_zones["date"] <= pd.Timestamp(past_end)
    ].reset_index(drop=True)
    pd.testing.assert_frame_equal(overlap, past.daily_zones.reset_index(drop=True))


def test_dataset_is_idempotent_and_contains_no_provider_identity_or_gps() -> None:
    end = date(2026, 8, 8)
    start = end - timedelta(days=89)
    kwargs = {
        "profile_identifier": "athlete-123",
        "session_salt": "session-salt",
        "parameters": _parameters(),
        "period_end": end,
        "days": 90,
        "loaded_at_utc": datetime(2026, 8, 8, 12, tzinfo=timezone.utc),
    }
    first = load_real_history(SyntheticHistoryClient(_specs(start, 90)), **kwargs)
    second = load_real_history(SyntheticHistoryClient(_specs(start, 90)), **kwargs)

    assert first.cache_key == second.cache_key
    pd.testing.assert_frame_equal(first.activities, second.activities)
    pd.testing.assert_frame_equal(first.daily_zones, second.daily_zones)
    pd.testing.assert_frame_equal(first.load_stats, second.load_stats)
    pd.testing.assert_frame_equal(first.readiness_history, second.readiness_history)
    rendered = repr(first).lower()
    for forbidden in (
        "athlete-123",
        "private real name",
        "real-000",
        "42.0",
        "latlng",
        "access_token",
        "refresh_token",
        "client_secret",
    ):
        assert forbidden not in rendered


def test_cached_real_dataset_rejects_stale_or_demo_values() -> None:
    end = date(2026, 8, 8)
    start = end - timedelta(days=89)
    dataset = load_real_history(
        SyntheticHistoryClient(_specs(start, 90)),
        profile_identifier="athlete-123",
        session_salt="session-salt",
        parameters=_parameters(),
        period_end=end,
        days=90,
    )
    assert (
        resolve_real_dataset(dataset, expected_cache_key=dataset.cache_key)
        is dataset
    )
    with pytest.raises(ValueError, match="stale"):
        resolve_real_dataset(dataset, expected_cache_key="0" * 64)
    with pytest.raises(ValueError, match="not loaded"):
        resolve_real_dataset({"source": DEMO_DATA_SOURCE}, expected_cache_key="0" * 64)


def test_short_history_is_explicitly_marked_as_limited() -> None:
    end = date(2026, 8, 8)
    start = end - timedelta(days=40)
    dataset = load_real_history(
        SyntheticHistoryClient(_specs(start, 41)),
        profile_identifier="athlete-123",
        session_salt="session-salt",
        parameters=_parameters(),
        period_end=end,
        days=41,
    )

    assert len(dataset.daily_loads) == 41
    assert any("40-дневно загряване" in warning for warning in dataset.warnings)


def test_real_history_application_layer_has_no_streamlit_dependency() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "intervals_inspector"
        / "real_data_source.py"
    ).read_text(encoding="utf-8")

    assert "import streamlit" not in source
    assert "st.session_state" not in source
