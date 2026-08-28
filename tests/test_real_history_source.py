from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
import pytest

from biathlon.constants import (
    AEROBIC_TREF_BOUNDS_MINUTES,
    FIXED_STRENGTH_TREF_MINUTES,
)
from biathlon.demo_data import generate_demo_bundle
from intervals_inspector.intervals_client import IntervalsAPIError, IntervalsResponse
from intervals_inspector.real_data_source import (
    DEMO_DATA_SOURCE,
    REAL_DATA_SOURCE,
    build_history_cache_key,
    build_real_load_view,
    build_real_recovery_view,
    load_real_history,
    recovery_parameter_fingerprint,
    resolve_real_dataset,
    is_strength_activity,
    validate_data_source,
)
from intervals_inspector.shadow_model import (
    configuration_with_overrides,
    default_shadow_configuration,
)


EXPECTED_TREF_BOUNDS = dict(AEROBIC_TREF_BOUNDS_MINUTES)


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
        assert include_intervals is True
        detail = _activity_detail(activity_id, self.specs[activity_id]["date"])
        for field in (
            "type",
            "sub_type",
            "elapsed_time",
            "moving_time",
            "icu_recording_time",
        ):
            if field in self.specs[activity_id]:
                detail[field] = self.specs[activity_id][field]
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


class FailingActivityClient(SyntheticHistoryClient):
    def __init__(
        self,
        specs: dict[str, dict[str, Any]],
        *,
        failing_activity_id: str,
        status_code: int,
    ) -> None:
        super().__init__(specs)
        self.failing_activity_id = failing_activity_id
        self.status_code = status_code

    def get_streams_result(self, activity_id: str) -> IntervalsResponse:
        if activity_id == self.failing_activity_id:
            retryable = self.status_code == 429 or self.status_code >= 500
            raise IntervalsAPIError(
                "sanitized provider failure",
                status_code=self.status_code,
                retry_after_seconds=37 if retryable else None,
                retryable=retryable,
                terminal=not retryable,
            )
        return super().get_streams_result(activity_id)


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


def test_systemic_activity_provider_failure_aborts_the_whole_history() -> None:
    end = date(2026, 8, 8)
    start = end - timedelta(days=40)
    specs = {
        "first-ok": {"date": start + timedelta(days=1)},
        "second-rate-limited": {"date": start + timedelta(days=2)},
    }

    with pytest.raises(IntervalsAPIError) as captured:
        load_real_history(
            FailingActivityClient(
                specs,
                failing_activity_id="second-rate-limited",
                status_code=429,
            ),
            profile_identifier="athlete-provider-failure",
            session_salt="session-salt",
            parameters=_parameters(),
            period_end=end,
            days=41,
        )

    assert captured.value.status_code == 429
    assert captured.value.retryable is True
    assert captured.value.retry_after_seconds == pytest.approx(37.0)


def test_explicitly_missing_activity_is_excluded_without_hiding_other_data() -> None:
    end = date(2026, 8, 8)
    start = end - timedelta(days=40)
    specs = {
        "available": {"date": start + timedelta(days=1)},
        "deleted": {"date": start + timedelta(days=2)},
    }

    dataset = load_real_history(
        FailingActivityClient(
            specs,
            failing_activity_id="deleted",
            status_code=410,
        ),
        profile_identifier="athlete-missing-activity",
        session_salt="session-salt",
        parameters=_parameters(),
        period_end=end,
        days=41,
    )

    assert dataset.processed_activities == 2
    assert dataset.excluded_activities == 1
    assert dataset.activities["quality_status"].tolist() == ["valid", "excluded"]


@pytest.mark.parametrize("callback_kind", ["metadata", "shadow"])
def test_external_activity_callback_failure_aborts_generation(
    callback_kind: str,
) -> None:
    end = date(2026, 8, 8)
    specs = {"activity": {"date": end}}

    def fail_callback(*args: Any) -> Mapping[str, Any] | None:
        del args
        raise ValueError("private persistence detail")

    kwargs: dict[str, Any] = {}
    if callback_kind == "metadata":
        kwargs["activity_metadata_collector"] = fail_callback
    else:
        kwargs["activity_shadow_processor"] = fail_callback

    with pytest.raises(RuntimeError, match="Activity .* callback failed") as captured:
        load_real_history(
            SyntheticHistoryClient(specs),
            profile_identifier="athlete-callback-failure",
            session_salt="session-salt",
            parameters=_parameters(),
            period_end=end,
            days=41,
            **kwargs,
        )

    assert isinstance(captured.value.__cause__, ValueError)


def _bounded_tref(zone: str, raw: float) -> float:
    lower, upper = EXPECTED_TREF_BOUNDS[zone]
    return min(max(float(raw), lower), upper)


def _assert_bounded_tref_is_shared_everywhere(dataset: Any) -> None:
    for zone, (lower, upper) in EXPECTED_TREF_BOUNDS.items():
        daily_rows = dataset.daily_zones.loc[
            dataset.daily_zones["zone"] == zone
        ].sort_values("date")
        effective_history: list[float] = []
        expected_raw: list[float] = []
        expected_tref: list[float] = []
        expected_bound: list[str] = []
        for effective in daily_rows["E_z"].astype(float):
            raw = (
                7.0 * float(pd.Series(effective_history[-40:]).mean())
                if effective_history
                else upper
            )
            expected_raw.append(raw)
            expected_tref.append(_bounded_tref(zone, raw))
            expected_bound.append(
                "lower" if raw < lower else "upper" if raw > upper else "none"
            )
            effective_history.append(effective)

        assert daily_rows["tref_raw"].to_numpy() == pytest.approx(
            expected_raw
        )
        assert daily_rows["tref_effective"].to_numpy() == pytest.approx(
            expected_tref
        )
        assert daily_rows["tref_bound_applied"].tolist() == expected_bound
        assert daily_rows["tref_effective"].between(lower, upper).all()

        expected_by_date = daily_rows.set_index("date")["tref_effective"]
        activity_values = dataset.activity_zones.loc[
            dataset.activity_zones["zone"] == zone,
            ["date", "tref_effective"],
        ]
        rolling_values = dataset.rolling_load.loc[
            dataset.rolling_load["component"] == zone
        ].sort_values("date")
        readiness_values = dataset.readiness_history.loc[
            dataset.readiness_history["component"] == zone
        ].sort_values("date")

        assert not activity_values.empty
        assert not rolling_values.empty
        assert not readiness_values.empty
        assert activity_values["tref_effective"].to_numpy() == pytest.approx(
            activity_values["date"].map(expected_by_date).to_numpy()
        )
        assert rolling_values["Tref"].to_numpy() == pytest.approx(
            expected_tref
        )
        assert readiness_values["Tref"].to_numpy() == pytest.approx(
            expected_tref
        )
        assert dataset.daily_loads[f"tref_used_{zone}"].to_numpy() == (
            pytest.approx(expected_tref)
        )
        current_raw = 7.0 * float(
            dataset.load_stats.loc[zone, "E40_daily"]
        )
        assert dataset.load_stats.loc[zone, "Tref"] == pytest.approx(
            _bounded_tref(zone, current_raw)
        )

    assert dataset.daily_loads["tref_used_STR"].to_numpy() == pytest.approx(
        FIXED_STRENGTH_TREF_MINUTES
    )
    assert dataset.load_stats.loc["STR", "Tref"] == pytest.approx(
        FIXED_STRENGTH_TREF_MINUTES
    )
    assert dataset.rolling_load.loc[
        dataset.rolling_load["component"] == "STR", "Tref"
    ].to_numpy() == pytest.approx(FIXED_STRENGTH_TREF_MINUTES)
    assert dataset.readiness_history.loc[
        dataset.readiness_history["component"] == "STR", "Tref"
    ].to_numpy() == pytest.approx(FIXED_STRENGTH_TREF_MINUTES)


def _with_old_history_hr(
    specs: dict[str, dict[str, Any]],
    *,
    period_end: date,
    old_hr: float,
) -> dict[str, dict[str, Any]]:
    cutoff = period_end - timedelta(days=6)
    rendered = {
        activity_id: dict(spec) for activity_id, spec in specs.items()
    }
    for spec in rendered.values():
        if spec["date"] >= cutoff or all(
            value is None for value in spec.get("hr", [old_hr])
        ):
            continue
        sample_count = len(spec.get("offsets", list(range(61))))
        spec["hr"] = [old_hr] * sample_count
    return rendered


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
        {"parameter.Z2.equivalence_slope_pp_per_bpm": 2.5}
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
    assert changed_configuration.equivalence_version == (
        configuration.equivalence_version
    )
    assert changed_configuration.fingerprint != configuration.fingerprint


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
    assert {"q_STR", "e_STR", "tref_used_STR"} <= set(dataset.daily_loads)
    assert dataset.daily_loads["q_STR"].eq(0.0).all()
    assert set(dataset.load_stats.index) == {"Z1", "Z2", "Z3", "Z4", "Z5", "STR"}
    assert set(dataset.load_readiness.index) == {"Z1", "Z2", "Z3", "Z4", "Z5", "STR"}
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
        "T_eq_z",
        "mean_effective_hr_bpm",
        "average_minute_value_percent",
        "direct_ratio",
        "cascade",
        "spillover",
        "E_z",
        "tref_raw",
        "tref_effective",
    } <= set(dataset.activity_zones)
    assert {"Q_z", "Qref_z"}.isdisjoint(dataset.activity_zones.columns)
    assert {"Q_z", "Qref_z"}.isdisjoint(dataset.daily_zones.columns)
    assert float(dataset.activity_zones["T_z"].sum()) > 0.0
    assert float(dataset.activity_zones["T_eq_z"].sum()) > 0.0
    assert float(dataset.activity_zones["cascade"].sum()) > 0.0
    assert float(dataset.activity_zones["spillover"].sum()) >= 0.0
    assert float(dataset.activity_zones["E_z"].sum()) > 0.0
    assert dataset.activity_zones.loc[
        dataset.activity_zones["T_z"] > 0.0,
        "mean_effective_hr_bpm",
    ].notna().all()
    assert dataset.activity_zones.loc[
        dataset.activity_zones["T_z"] > 0.0,
        "average_minute_value_percent",
    ].notna().all()
    daily_equivalent = dataset.daily_zones.pivot(
        index="date",
        columns="zone",
        values="T_eq_z",
    ).reindex(dataset.daily_loads.index)
    for zone in EXPECTED_TREF_BOUNDS:
        assert dataset.daily_loads[f"q_{zone}"].to_numpy() == pytest.approx(
            daily_equivalent[zone].to_numpy()
        )
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
    _assert_bounded_tref_is_shared_everywhere(dataset)
    assert build_real_load_view(dataset)["daily_zones"] is dataset.daily_zones
    assert (
        build_real_recovery_view(dataset)["readiness_history"]
        is dataset.readiness_history
    )


def test_strength_activity_uses_recording_duration_and_never_hr_zones() -> None:
    end = date(2026, 8, 8)
    start = end - timedelta(days=40)
    strength_day = end - timedelta(days=2)
    specs = {
        "strength": {
            "date": strength_day,
            "type": "WeightTraining",
            "moving_time": 600,
            "icu_recording_time": 1800,
            "elapsed_time": 2100,
            "hr": [175.0] * 61,
        },
        "run": {"date": end, "type": "Run", "hr": [145.0] * 61},
    }

    dataset = load_real_history(
        SyntheticHistoryClient(specs),
        profile_identifier="athlete-strength",
        session_salt="session-salt",
        parameters=_parameters(),
        period_end=end,
        days=41,
    )

    activity = dataset.activities.loc[
        dataset.activities["sport"] == "WeightTraining"
    ].iloc[0]
    assert activity["duration_min"] == pytest.approx(30.0)
    assert activity["strength_time_min"] == pytest.approx(30.0)
    strength_date = pd.Timestamp(strength_day)
    assert dataset.daily_loads.loc[strength_date, "q_STR"] == pytest.approx(30.0)
    assert dataset.daily_loads.loc[strength_date, "e_STR"] == pytest.approx(30.0)
    assert dataset.daily_loads.loc[
        strength_date, [f"q_{zone}" for zone in EXPECTED_TREF_BOUNDS]
    ].sum() == pytest.approx(0.0)
    strength_ref = str(activity["activity_ref"])
    aerobic_rows = dataset.activity_zones.loc[
        dataset.activity_zones["activity_ref"] == strength_ref
    ]
    assert len(aerobic_rows) == 5
    assert aerobic_rows[["T_z", "T_eq_z", "E_z"]].to_numpy().sum() == pytest.approx(0.0)
    assert dataset.load_stats.loc["STR", "Tref"] == pytest.approx(56.0)
    assert dataset.load_readiness.loc["STR", "readiness"] < 100.0


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        ({"type": "WeightTraining"}, True),
        ({"type": "weight training"}, True),
        ({"type": "Workout", "sub_type": "StrengthTraining"}, True),
        ({"type": "CoreTraining"}, True),
        ({"type": "Crossfit"}, False),
        ({"type": "HighIntensityIntervalTraining"}, False),
        ({"type": "Workout"}, False),
        ({"type": "Run", "name": "Strength after running"}, False),
    ],
)
def test_strength_classification_is_explicit_and_does_not_use_activity_name(
    detail: dict[str, Any], expected: bool
) -> None:
    assert is_strength_activity(detail) is expected


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


def test_tref_uses_only_previous_calendar_days_for_each_history_day() -> None:
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
    expected = 7.0 * float(zone_history["E_z"].mean())
    actual = dataset.daily_zones.loc[
        (dataset.daily_zones["date"] == current)
        & (dataset.daily_zones["zone"] == "Z2"),
        "tref_history_value",
    ].iloc[0]
    assert len(zone_history) == 40
    assert actual == pytest.approx(expected)
    same_day_h40 = dataset.activity_zones.loc[
        (dataset.activity_zones["date"] == current)
        & (dataset.activity_zones["zone"] == "Z2"),
        "tref_history_value",
    ]
    same_day_tref = dataset.activity_zones.loc[
        (dataset.activity_zones["date"] == current)
        & (dataset.activity_zones["zone"] == "Z2"),
        "tref_effective",
    ]
    assert len(same_day_h40) == 2
    assert all(value == pytest.approx(expected) for value in same_day_h40)
    assert same_day_tref.to_numpy() == pytest.approx(
        _bounded_tref("Z2", expected)
    )


def test_h40_seven_forty_and_bounded_tref_react_to_real_history() -> None:
    end = date(2026, 8, 8)
    start = end - timedelta(days=89)
    base_specs = _specs(start, 90)
    low_old_history = load_real_history(
        SyntheticHistoryClient(
            _with_old_history_hr(base_specs, period_end=end, old_hr=126.0)
        ),
        profile_identifier="athlete-low-old-history",
        session_salt="session-salt",
        parameters=_parameters(),
        period_end=end,
        days=90,
    )
    high_old_history = load_real_history(
        SyntheticHistoryClient(
            _with_old_history_hr(base_specs, period_end=end, old_hr=145.0)
        ),
        profile_identifier="athlete-high-old-history",
        session_salt="session-salt",
        parameters=_parameters(),
        period_end=end,
        days=90,
    )

    def latest_h40(dataset: Any) -> float:
        return float(
            dataset.daily_zones.loc[
                (dataset.daily_zones["date"] == pd.Timestamp(end))
                & (dataset.daily_zones["zone"] == "Z2"),
                "tref_history_value",
            ].iloc[0]
        )

    assert latest_h40(high_old_history) > latest_h40(low_old_history)
    assert high_old_history.load_stats.loc["Z2", "E40_daily"] > (
        low_old_history.load_stats.loc["Z2", "E40_daily"]
    )
    assert high_old_history.load_stats.loc["Z2", "index_7_40"] < (
        low_old_history.load_stats.loc["Z2", "index_7_40"]
    )
    for dataset in (low_old_history, high_old_history):
        expected_current = _bounded_tref(
            "Z2", 7.0 * float(dataset.load_stats.loc["Z2", "E40_daily"])
        )
        assert dataset.load_stats.loc["Z2", "Tref"] == pytest.approx(
            expected_current
        )
        _assert_bounded_tref_is_shared_everywhere(dataset)


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
