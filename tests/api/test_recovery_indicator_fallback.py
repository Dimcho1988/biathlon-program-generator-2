from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from apps.api import model_service, planning_history_estimate, recovery_load_projection
from apps.api.model_schemas import initial_settings
from biathlon import planning_history, recovery_v2, speed_equivalence, unified_load
from tests.api.test_load_progression import configured
from tests.test_unified_zone_load import BOUNDS, profile, streams

TODAY = date(2026, 10, 29)
ZONES = speed_equivalence.ZONES


def source(unknown=60.):
    days = [(TODAY-timedelta(days=n)).isoformat() for n in range(40, -1, -1)]
    ledger = unified_load.integrate(streams(None, 16., seconds=60), bounds=BOUNDS,
        sport="Run", parameters=speed_equivalence.prepare(profile()), duration_s=60+unknown)
    activity = {"activity_ref": "recorded", "date": days[-2], "sport": "Run",
        "duration_min": (60+unknown)/60, "quality_status": "limited", "hr_coverage_percent": 0.,
        "load_source": {k: v for k, v in ledger.items() if k != "zones"},
        "zones": [{**z, "effective_load": z["equivalent_time_min"]} for z in ledger["zones"]]}
    return {"period_start": days[0], "period_end": days[-1], "load_selection": {"version": unified_load.VERSION},
        "quality": {"limited_activities": 1, "excluded_activities": 0}, "activities": [activity],
        "daily": [{"date": d, "zone": z["zone"], "effective_load": z["effective_load"] if d == activity["date"] else 0.}
                  for d in days for z in activity["zones"]],
        "strength": {"daily": [{"date": d, "effective_load": 0.} for d in days]}}


class Repository:
    def __init__(self, history):
        self.history = history
        self.calendar = deepcopy(history["activities"])

    def planning_profile_rows(self, alias):
        return [{"kind": "PROFILE", "entry_key": "management-profile-v1", "revision": 1, "payload": configured()}]

    def active_trainability_calendar(self, alias, start, end):
        assert type(start) is date and type(end) is date
        return {"activities": self.calendar}

    def athlete_settings(self, alias):
        return SimpleNamespace(timezone="UTC")


def test_full_speed_coverage_overrides_hr_only_quality_without_estimating():
    history = source(0.)
    quality = planning_history.recovery_coverage(history, TODAY)
    assert quality["complete"] and quality["readiness_available"]
    assert quality["readiness_basis"] == "RECORDED_LOAD" and not quality["estimated"]
    assert quality["coverage_percent"] == 100. and quality["unknown_seconds"] == 0.
    estimated, info = planning_history_estimate.prepare(history, history["activities"], configured(), TODAY)
    assert info["supported"] and not info["estimated"]
    assert estimated["activities"] == history["activities"]


def test_old_identified_quality_is_outside_recovery_window_but_unidentified_is_not():
    history = source(0.)
    old = deepcopy(history["activities"][0])
    old.update(activity_ref="old", date=(TODAY-timedelta(days=45)).isoformat())
    old["load_source"]["unknown_seconds"] = 100.
    history["activities"].append(old)
    history["quality"]["limited_activities"] = 2
    assert planning_history.recovery_coverage(history, TODAY)["complete"]
    history["quality"]["excluded_activities"] = 1
    quality = planning_history.recovery_coverage(history, TODAY)
    assert not quality["complete"] and quality["unidentified_partial_activities"] == 1


def test_supported_estimate_uses_identical_planner_ledger_and_keeps_measured_gap():
    history = source()
    saved = deepcopy(history)
    repository = Repository(history)
    coverage = planning_history.recovery_coverage(history, TODAY)
    estimated, quality = recovery_load_projection.prepare(repository, "test", history, TODAY, coverage)
    planner, evidence = planning_history_estimate.prepare(history, repository.calendar, configured(), TODAY)
    assert {k:v for k,v in estimated.items() if k != "planning_history"} == {k:v for k,v in planner.items() if k != "planning_history"}
    assert history == saved
    assert not quality["complete"] and quality["readiness_available"] and quality["estimated"]
    assert quality["readiness_basis"] == "ESTIMATED_LOAD"
    assert quality["recorded_seconds"] == 120 and quality["unknown_seconds"] == 60
    assert quality["coverage_percent"] == 50
    assert quality["estimation"]["estimated_minutes"] == evidence["estimated_minutes"] == 1.


def test_older_dated_missing_activity_does_not_block_recent_supported_estimate():
    history = source()
    history["period_start"] = (TODAY-timedelta(days=60)).isoformat()
    history["quality"]["excluded_activities"] = 1
    repository = Repository(history)
    repository.calendar.append({"activity_ref": "old", "date": (TODAY-timedelta(days=45)).isoformat(),
        "sport": "Run", "duration_min": None, "quality_status": "provider_missing"})
    ledger, quality = recovery_load_projection.prepare(repository, "test", history, TODAY,
        planning_history.recovery_coverage(history, TODAY))
    assert quality["readiness_available"] and quality["estimated"]
    assert quality["estimation"]["unresolved_activity_count"] == 0
    assert ledger["planning_history"]["older_unresolved_activity_count"] == 1


def test_catalog_can_date_an_old_exclusion_without_requiring_estimated_recovery():
    history = source(0.)
    history["period_start"] = (TODAY-timedelta(days=60)).isoformat()
    history["quality"]["excluded_activities"] = 1
    repository = Repository(history)
    repository.calendar.append({"activity_ref": "old", "date": (TODAY-timedelta(days=45)).isoformat(),
        "sport": "Run", "quality_status": "provider_missing"})
    ledger, quality = recovery_load_projection.prepare(repository, "test", history, TODAY,
        planning_history.recovery_coverage(history, TODAY))
    assert ledger is history and quality["complete"] and not quality["estimated"]
    assert quality["readiness_basis"] == "RECORDED_LOAD"


@pytest.mark.parametrize("days", [1, None])
def test_catalog_only_counted_omission_inside_window_or_without_date_stays_blocked(days):
    history = source()
    history["quality"]["excluded_activities"] = 1
    repository = Repository(history)
    repository.calendar.append({"activity_ref": "missing", "date": (TODAY-timedelta(days=days)).isoformat() if days else None,
        "sport": "Run", "duration_min": 10., "quality_status": "provider_missing"})
    unchanged, quality = recovery_load_projection.prepare(repository, "test", history, TODAY,
        planning_history.recovery_coverage(history, TODAY))
    assert unchanged is history
    assert not quality["readiness_available"] and not quality["estimated"]
    assert quality["recorded_seconds"] is None and quality["unknown_seconds"] is None


@pytest.mark.parametrize("missing", ["calendar", "provider", "duration", "unbalanced", "unidentified", "profile", "catalog"])
def test_estimate_never_resolves_unknown_records_or_durations(missing):
    history = source()
    repository = Repository(history)
    if missing == "calendar":
        history["daily"].pop(0)
    elif missing == "provider":
        repository.calendar.append({"activity_ref": "provider", "date": (TODAY-timedelta(days=1)).isoformat(),
            "sport": "Run", "duration_min": 60., "quality_status": "provider_missing"})
    elif missing == "duration":
        del history["activities"][0]["load_source"]["recorded_seconds"]
    elif missing == "unbalanced":
        history["activities"][0]["load_source"]["recorded_seconds"] += 1
    elif missing == "unidentified":
        history["quality"]["excluded_activities"] = 1
    elif missing == "profile":
        repository.planning_profile_rows = lambda _: []
    else:
        repository.active_trainability_calendar = None
    saved = deepcopy(history)
    _, quality = recovery_load_projection.prepare(repository, "test", history, TODAY,
        planning_history.recovery_coverage(history, TODAY))
    assert not quality["readiness_available"] and not quality["estimated"]
    assert quality["readiness_basis"] == "UNAVAILABLE"
    assert history == saved


def test_projection_current_daily_and_forecast_share_estimated_ledger(monkeypatch):
    monkeypatch.setenv("ONFLOWS_RECOVERY_V2_ENABLED", "true")
    from apps.api import component_load_projection
    monkeypatch.setattr(component_load_projection, "project_snapshot", lambda repo, alias, snapshot: deepcopy(snapshot))
    history = source()
    repository = Repository(history)
    original = {"load_history": history, "training_status": {"zones": [{"zone": z} for z in ZONES]}}
    projected = model_service.project_recovery(repository, "test", original,
        now=datetime(2026, 10, 29, 12, tzinfo=timezone.utc), config={"expected_revision": 0, "zones": initial_settings()})
    estimated, _ = planning_history_estimate.prepare(history, repository.calendar, configured(), TODAY)
    rows = estimated["daily"] + [{**r, "zone": "STR"} for r in estimated["strength"]["daily"]]
    expected = recovery_v2.simulate(rows, initial_settings(), target=TODAY)
    result = projected["recovery_history"]
    for key in ("current", "daily", "forecast"):
        assert result[key] == expected[key]
    assert result["data_quality"]["readiness_basis"] == "ESTIMATED_LOAD"
    assert "ESTIMATED_LOAD_RECOVERY" in result["warnings"]
    assert projected["load_history"] == original["load_history"]
    for row in projected["training_status"]["zones"]:
        current = next(r for r in result["current"] if r["zone"] == row["zone"])
        assert row["recovery_readiness_percent"] == current["readiness_percent"]


@pytest.mark.parametrize("flag", ["HR_ARTIFACT", "INVALID_HR", "MISSING_HR", "HR_OUT_OF_RANGE", "SENSOR_DROPOUT", "SENSOR_ERROR"])
def test_invalid_raw_and_hrmod_always_yield_to_available_speed(flag):
    shadow = streams(180., 16.)
    for row in shadow["timeseries"]:
        row.update(hrmod_final_bpm=185., quality_flags=[flag])
    result = unified_load.integrate(shadow, bounds=BOUNDS, sport="Run", parameters=speed_equivalence.prepare(profile()))
    assert result["source_seconds"] == {"HEART_RATE": 0., "SPEED": 60.}
    assert result["fallback_seconds"] == 60. and result["unknown_seconds"] == 0.
    shadow["speed_test_series"] = []
    unknown = unified_load.integrate(shadow, bounds=BOUNDS, sport="Run", parameters=speed_equivalence.prepare(profile()))
    assert unknown["unknown_seconds"] == 60. and unknown["active_seconds"] == 0.


@pytest.mark.parametrize("raw_hr", [20., 27., 301.])
def test_raw_outside_existing_pairing_range_falls_back_without_cleaner_flags(raw_hr):
    shadow = streams(raw_hr, 16.)
    for row in shadow["timeseries"]:
        row["hrmod_final_bpm"] = 160.
    original = deepcopy(shadow)
    result = unified_load.integrate(shadow, bounds=BOUNDS, sport="Run", parameters=speed_equivalence.prepare(profile()))
    assert result["source_seconds"] == {"HEART_RATE": 0., "SPEED": 60.}
    assert result["fallback_seconds"] == 60. and result["rest_seconds"] == 0.
    assert shadow == original
    shadow["speed_test_series"] = []
    unknown = unified_load.integrate(shadow, bounds=BOUNDS, sport="Run", parameters=speed_equivalence.prepare(profile()))
    assert unknown["unknown_seconds"] == 60. and unknown["rest_seconds"] == 0.


@pytest.mark.parametrize("raw_hr", [30., 300.])
def test_existing_raw_plausibility_bounds_stay_inclusive(raw_hr):
    assert unified_load.observed_hr_valid({"hr_raw_bpm": raw_hr})


def test_smart_hr_span_cannot_interpolate_through_invalid_endpoint():
    shadow = streams(180., 16., seconds=5)
    shadow["timeseries"] = [{"elapsed_s": 0., "hr_raw_bpm": 180., "hr_interval_classification": "smart_recording", "hr_interval_end_s": 5.},
                            {"elapsed_s": 5., "hr_raw_bpm": 185., "quality_flags": ["HR_ARTIFACT"]}]
    result = unified_load.integrate(shadow, bounds=BOUNDS, sport="Run", parameters=speed_equivalence.prepare(profile()), duration_s=5.)
    assert result["source_seconds"] == {"HEART_RATE": 0., "SPEED": 5.}


def test_unrelated_missing_speed_flag_does_not_invalidate_real_hr():
    shadow = streams()
    for row in shadow["timeseries"]:
        row["quality_flags"] = ["missing_value", "invalid_source_value", "SPEED_ARTIFACT", "SPEED_SENSOR_ERROR", "SPEED_SENSOR_DROPOUT"]
    result = unified_load.integrate(shadow, bounds=BOUNDS, sport="Run", parameters=speed_equivalence.prepare(profile()))
    assert result["source_seconds"]["HEART_RATE"] == 60.
