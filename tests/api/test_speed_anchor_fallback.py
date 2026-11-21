"""One selected HR/speed anchor for display, load and prescription consumers."""
from copy import deepcopy
from datetime import datetime, timedelta

import pytest

from apps.api import component_load_context, model_service as service
from biathlon import speed_anchor_policy, zone_profile
from tests.api.test_speed_history_window import NOW, Repository
from tests.api.test_initial_threshold_integration import Repository as InitialRepository, NOW as INITIAL_NOW


@pytest.fixture(autouse=True)
def fixed_time(monkeypatch):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz)
    monkeypatch.setattr(service, "datetime", FixedDatetime)


@pytest.mark.parametrize("age,current", [(14, True), (15, False), (28, False)])
@pytest.mark.parametrize("tests", [1, 2])
def test_stale_paired_index_yields_to_accepted_test_mapping(age, current, tests):
    repo = Repository()
    repo.add("last-hr", -age, value=5.)
    repo.test(source="MANUAL", duration_s=145., speed_kmh=800/145*3.6)
    if tests == 2:
        repo.test(source="MANUAL", duration_s=1000., speed_kmh=14.4)
    original = deepcopy(repo.summaries)
    full = service.speed_view(repo, "ath-test", "Run")
    fast = service.speed_view(repo, "ath-test", "Run", _capacity_only=True)
    band = full["index_freshness"]["bands"]["Z3"]
    assert band["status"] == ("CURRENT" if current else "STALE")
    assert band["age_days"] == age
    assert band["observed_index"] == 5.
    assert full["active_test_count"] == tests
    assert full["initial_threshold"]["superseded_by"] == "TESTS"
    assert full["speed_role"] == "FLAT_EQUIVALENT"
    assert full["index_summary"]["Z3"]["index"] == (5. if current else None)
    assert full["index_admission"]["used"] == (1 if current else 0)
    assert full["index_window"]["last_activity_date"] == (repo.activities[0]["local_date"] if current else None)
    if not current:
        assert "STALE_PAIRED_INDEX_EXCLUDED" in full["warnings"]
        assert full["dosing_model"]["status"] == "UNAVAILABLE"
        assert all(z["source"] == "INITIAL_V50_V6_ESTIMATE" for z in full["speed_zones"]["zones"][:4])
    assert full["index_freshness"] == fast["index_freshness"]
    assert full["index_summary"] == fast["index_summary"]
    assert component_load_context.context_from_speed_view(full) == component_load_context.context_from_speed_view(fast)
    profile = full["speed_zones"]
    assert profile["calibration_eligible"] is False and profile["is_measured_hr"] is False
    for hr in profile["reference_bounds_bpm"]:
        speed = zone_profile.speed_for_hr(profile, hr)
        assert zone_profile.hr_for_speed(profile, speed) == pytest.approx(hr)
    assert repo.summaries == original and repo.saved == []


def test_each_band_requires_recent_support_and_may_use_current_general_index():
    repo = Repository()
    repo.test(source="MANUAL")
    repo.add("stale-zones", -28, value=5.)
    repo.add("recent-general", -1, value=5.2)
    for band in repo.summaries["recent-general"]["trainability_index"]["zones"]:
        band["valid"] = False
    result = service.speed_view(repo, "ath-test", "Run")
    assert result["index_freshness"]["bands"]["GENERAL"]["status"] == "CURRENT"
    assert result["index_freshness"]["bands"]["Z3"]["status"] == "STALE"
    assert result["index_summary"]["Z3"]["index"] is None
    assert result["index_summary"]["GENERAL"]["count"] == 2
    assert result["speed_zones"]["zones"][2]["source"] == "GENERAL_PAIRED_INDEX"


def test_recent_invalid_hr_cannot_refresh_stale_paired_indices():
    repo = Repository()
    repo.test(source="MANUAL")
    repo.add("stale", -28)
    repo.add("invalid-hr", -1, changes={"signal_quality": {"status": "EXCLUDED", "reason": "HR_SIGNAL_SUSPECT"}})
    result = service.speed_view(repo, "ath-test", "Run")
    assert result["index_freshness"]["last_observation_date"] == (NOW.date()-timedelta(days=28)).isoformat()
    assert result["index_window"]["last_activity_date"] is None
    assert all(row["index"] is None for row in result["index_summary"].values())
    assert result["speed_zones"]["zones"][2]["source"] == "INITIAL_V50_V6_ESTIMATE"


def test_explicit_hr_artifact_cannot_refresh_paired_support_even_with_stable_raw_values():
    from apps.api.trainability import compute_trainability
    from tests.api.test_trainability import samples
    from tests.api.test_speed_history_window import BOUNDS, KEY, VF_VERSION

    repo = Repository()
    repo.test(source="MANUAL")
    repo.add("old", -28)
    repo.add("artifact", -1)
    hr, speed = samples(hr=150)
    for row in hr:
        row["quality_flags"] = ["HR_ARTIFACT"]
    repo.summaries["artifact"]["trainability_index"] = compute_trainability(
        hr, speed, zone_bounds_bpm=BOUNDS, hrmax_bpm=190, activity_duration_s=600,
        comparison_key=KEY, source_versions={"vflat": VF_VERSION})
    result = service.speed_view(repo, "ath-test", "Run")
    assert result["index_freshness"]["bands"]["Z3"]["age_days"] == 28
    assert result["index_freshness"]["bands"]["Z3"]["status"] == "STALE"
    assert result["index_summary"]["Z3"]["index"] is None
    assert result["speed_zones"]["zones"][2]["source"] == "INITIAL_V50_V6_ESTIMATE"


def test_freshness_is_relative_to_historical_calibration_day():
    repo = Repository()
    repo.test(source="MANUAL")
    repo.add("old-now-current-then", -28)
    inputs = service.speed_view_inputs(repo, "ath-test")
    activity_day = NOW.date()-timedelta(days=14)
    past = service.speed_view(repo, "ath-test", "Run",
        _inputs=service.historical_speed_inputs(inputs, activity_day))
    current = service.speed_view(repo, "ath-test", "Run", _inputs=inputs)
    assert past["index_freshness"]["as_of"] == (activity_day-timedelta(days=1)).isoformat()
    assert past["index_freshness"]["bands"]["Z3"]["age_days"] == 13
    assert past["index_summary"]["Z3"]["index"] == 5.
    assert current["index_summary"]["Z3"]["index"] is None
    assert past["historical_calibration"]["retrospective_test_keys"] == ["0"]


@pytest.mark.parametrize("years,expected", [(4, "INITIAL_HEURISTIC"), (None, "UNAVAILABLE")])
def test_no_tests_and_stale_index_use_initial_policy_only_with_complete_inputs(monkeypatch, years, expected):
    class InitialDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return INITIAL_NOW.astimezone(tz)
    monkeypatch.setattr(service, "datetime", InitialDatetime)
    repo = InitialRepository()
    repo.profile = {} if years is None else {"training_experience_years": years}
    repo.add_index()
    repo.calendar["activities"][0]["local_date"] = (INITIAL_NOW.date()-timedelta(days=22)).isoformat()
    before = deepcopy(repo.summaries)
    result = service.speed_view(repo, "ath-test", "Run")
    assert result["status"] == expected
    assert result["active_test_keys"] == []
    assert result["index_freshness"]["version"] == speed_anchor_policy.VERSION
    assert result["index_window"]["last_activity_date"] is None
    if years is not None:
        assert result["speed_role"] == "RAW_MOVING_SPEED"
        assert result["initial_threshold"]["calibration_eligible"] is False
    else:
        assert result["initial_threshold"]["reason"] == "TRAINING_YEARS_REQUIRED"
    assert repo.summaries == before


def test_new_valid_sensor_observation_restores_paired_mapping_without_losing_tests():
    repo = Repository()
    repo.test(source="MANUAL")
    repo.add("old", -28)
    before = service.speed_view(repo, "ath-test", "Run")
    repo.add("new", -1)
    after = service.speed_view(repo, "ath-test", "Run")
    assert before["speed_zones"]["zones"][2]["source"] == "INITIAL_V50_V6_ESTIMATE"
    assert after["speed_zones"]["zones"][2]["source"] == "PAIRED_INDEX"
    assert before["active_test_keys"] == after["active_test_keys"] == ["0"]
    assert after["index_summary"]["Z3"]["count"] == 2
    assert after["index_freshness"]["excluded_bands"] == []


@pytest.mark.parametrize("sport", ["Run", "Ride"])
@pytest.mark.parametrize("maximum_source", ["MANUAL", "TANAKA"])
def test_without_hr_tests_map_to_saved_hrmax_percentages_in_each_sport(sport, maximum_source):
    from apps.api.cloud import AthleteModelSettings
    from biathlon.hrmax import tanaka
    from biathlon.hr_zones import bounds_from_hrmax
    from biathlon.sport_heart_rate import reference_offset

    maximum = 190 if maximum_source == "MANUAL" else tanaka(28)
    bounds = bounds_from_hrmax(maximum, zone_profile.HR_PERCENTAGES)
    settings = AthleteModelSettings(bounds, "Europe/Sofia", maximum,
        hr_zone_source="AUTOMATIC_HRMAX", hr_zone_percentages=zone_profile.HR_PERCENTAGES,
        age_years=28, hrmax_source=maximum_source).validate()
    repo = Repository()
    repo.athlete_settings = lambda alias: settings
    repo.test(sport=sport, source="MANUAL", speed_kmh=22.)
    other = "Ride" if sport == "Run" else "Run"
    repo.test(sport=other, source="MANUAL", speed_kmh=40.)
    repo.add("other-sport-hr", -1, sport=other)

    result = service.speed_view(repo, "ath-test", sport)
    profile = result["speed_zones"]
    offset = reference_offset(sport)
    assert result["active_test_keys"] == ["0"]
    assert result["index_admission"]["activities"] == 0
    assert result["index_freshness"]["last_current_observation_date"] is None
    assert profile["reference_hrmax_bpm"] == maximum
    assert profile["reference_bounds_bpm"] == list(bounds)
    assert result["sport_hr_policy"]["sport_zone_bounds_bpm"] == [b-offset for b in bounds]
    assert all(z["source"] == "INITIAL_V50_V6_ESTIMATE" for z in profile["zones"][:4])
    for i, row in enumerate(result["hr_model"]["zones"][:4]):
        assert row["hr_bpm"] == bounds[i+1]-offset
        assert row["speed_kmh"] == pytest.approx(zone_profile.initial_speed(
            100*bounds[i+1]/maximum, profile["v50_kmh"], profile["v6_kmh"], cycling=sport=="Ride"))
    target_hr = bounds[3]-offset
    forward = service.speed_view(repo, "ath-test", sport, hr_bpm=target_hr)["prediction"]
    inverse = service.speed_view(repo, "ath-test", sport, speed_kmh=forward["speed_kmh"])["prediction"]
    assert inverse["estimated_hr_bpm"] == pytest.approx(target_hr)
    assert repo.athlete_settings("ath-test") == settings and repo.saved == []
