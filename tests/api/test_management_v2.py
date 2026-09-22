from copy import deepcopy
from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from apps.api import training_plan_engine as engine
from apps.api.management_schemas import ManagementProfile, IntervalDoseProfile
from biathlon import training_targets
from biathlon.periodization import build_periodization
from biathlon.training_methods import resolved_methods
from tests.api.test_training_plan_engine import Repository, NOW, TODAY, profile, reference_speed, supported_speed


def interval(zone="Z4", **changes):
    return {"zone": zone, "sport": "Run", "continuous_capacity_min": 12., "assessed_on": TODAY.isoformat(),
            "effort": "Силно, контролирано и повторяемо усилие", "work_seconds": 180 if zone == "Z4" else 30,
            "recovery_seconds": 180 if zone == "Z4" else 30, "min_repetitions": 3 if zone == "Z4" else 6,
            "max_repetitions": 5 if zone == "Z4" else 12, "total_capacity_ratio": 1.25,
            "reserve_repetitions": 2, "target_speed_kmh": None, **changes}


@pytest.fixture(autouse=True)
def speed_stub(monkeypatch):
    monkeypatch.setattr(engine.model_service, "speed_view", reference_speed)


def plan(repo=None, **changes):
    return engine.generate_plan(repo or Repository(), "athlete", profile(age_years=30, training_experience_years=5, **changes),
                                start_date=TODAY + timedelta(days=1), now=NOW)


def test_effort_profile_can_exceed_continuous_capacity_with_whole_repetitions():
    repo = Repository()
    repo.accents.update(accent_mode="MANUAL", manual_components=["Z4"])
    result = plan(repo, interval_profiles=[interval()])
    high = next(d["session"] for d in result["days"] if d["session"] and d["session"]["zone"] == "Z4")
    work = [b for b in high["blocks"] if b["kind"] == "WORK"]
    rests = [b for b in high["blocks"] if b["kind"] == "RECOVERY"]
    assert len(work) == 5 and len(rests) == 4
    assert high["main_work_minutes"] == 15 > high["dose_evidence"]["capacity_minutes"]
    assert high["total_minutes"] == 15 + 12 + 15 + 10
    assert all(b["target_hr_bpm"] is None and b["duration_min"] == 3 for b in work)
    assert high["canonical_effective_load"]["STR"] == 0


def test_direct_supported_speed_capacity_precedes_effort_fallback_without_hr_index():
    repo = Repository()
    speed = supported_speed(repo.settings)
    speed["index_summary"] = {}
    method = next(m for m in resolved_methods(profile(interval_profiles=[interval(target_speed_kmh=22, speed_basis="FLAT_EQUIVALENT")])) if m["structure"] == "METABOLIC_INTERVALS")
    cap = engine.capacity_for(method, repo.settings, speed, (None, [], []), TODAY)
    assert cap["capacity_source"] == "SPEED_DURATION"
    assert cap["capacity_minutes"] == pytest.approx(10.)
    assert cap["target_hr_bpm"] is None
    method["interval_profile"]["speed_basis"] = "ACTUAL"
    assert engine.capacity_for(method, repo.settings, speed, (None, [], []), TODAY)["capacity_source"] == "COACH_EFFORT_CAPACITY"


@pytest.mark.parametrize("change", [{"total_capacity_ratio": .1}, {"continuous_capacity_min": 2.},
                                    {"max_repetitions": 2}, {"reserve_repetitions": 0}])
def test_invalid_coupled_interval_profiles_are_rejected(change):
    with pytest.raises(ValidationError):
        IntervalDoseProfile.model_validate(interval(**change))


def test_strength_work_rests_and_transitions_are_not_aerobic_double_counts():
    repo = Repository()
    method = next(m for m in resolved_methods(profile(strength_enabled=True)) if m["structure"] == "STRENGTH_CIRCUIT")
    evidence = engine.capacity_for(method, repo.settings, None, (None, [], []), TODAY)
    blocks = engine._blocks(method, 6., evidence, repo.settings)
    direct, effective, _ = engine._canonical_load(blocks, repo.settings, [], TODAY)
    assert sum(b["duration_min"] for b in blocks if b["kind"] == "WORK") == pytest.approx(6.)
    assert sum(b["duration_min"] for b in blocks) == pytest.approx(31.)
    assert direct["STR"] == pytest.approx(16.)  # Work + transitions + between-round rest, exactly once.
    assert all(direct[z] == 0 for z in ("Z2", "Z3", "Z4", "Z5"))
    assert effective["STR"] == pytest.approx(direct["STR"])


def test_mixed_high_session_rejects_incomplete_minimum_even_with_available_minutes():
    repo = Repository()
    method = next(m for m in resolved_methods(profile(interval_profiles=[interval()])) if m["structure"] == "THRESHOLD_HIGH")
    evidence = {"target_hr_bpm": 155, "target_speed_kmh": None, "combination_high_work_cap": 7.5, "primary_requested_work": 12}
    # Three 3-min reps cannot fit a 7.5-min high block. It must not round up.
    assert engine._blocks(method, 12, evidence, repo.settings) == []


def test_forecasts_do_not_self_increase_development_reference():
    repo = Repository()
    actual = engine._daily_rows(repo.envelope["snapshot_payload"]["load_history"], TODAY)
    reference = training_targets.development_reference(actual, TODAY, TODAY, 4)
    before = deepcopy(reference)
    goals = training_targets.component_targets(reference, profile(progression_percent=5), ["Z3"], 2, 4, "GENERAL_PREPARATION", False)
    forecast = engine._with_forecast_day(actual, TODAY+timedelta(days=1), {"Z3": 1000})
    budget = engine._budgets(forecast, TODAY+timedelta(days=2), actual_rows=actual, targets=goals)
    assert reference == before
    assert goals["Z3"]["target"] == pytest.approx(reference["weekly"]["Z3"] * 1.05)
    assert budget["Z3"]["deficit_effective"] == 0
    assert goals["Z2"]["target"] == reference["weekly"]["Z2"]


def test_race_duration_changes_specific_z3_intensity_without_a_second_index_factor():
    repo = Repository()
    repo.events[0].update(start_date=(TODAY+timedelta(days=35)).isoformat(), end_date=(TODAY+timedelta(days=35)).isoformat())
    repo.accents.update(accent_mode="MANUAL", manual_components=["Z3"])
    short = plan(repo, race_duration_min=20)
    long = plan(repo, race_duration_min=120)
    a = next(d["session"] for d in short["days"] if d["session"] and d["session"]["zone"] == "Z3")
    b = next(d["session"] for d in long["days"] if d["session"] and d["session"]["zone"] == "Z3")
    assert a["dose_evidence"]["target_hr_bpm"] > b["dose_evidence"]["target_hr_bpm"]
    assert a["dose_evidence"]["specificity"]["focus"] != b["dose_evidence"]["specificity"]["focus"]


def test_last_days_of_program_and_explicit_transition_are_supported():
    result = plan(horizon_mode="MANUAL", program_end=(TODAY+timedelta(days=3)).isoformat())
    assert len(result["days"]) == 3 and result["end_date"] == (TODAY+timedelta(days=3)).isoformat()
    race = {"event_type": "MAIN_RACE", "start_date": (TODAY+timedelta(days=10)).isoformat(), "end_date": (TODAY+timedelta(days=10)).isoformat()}
    periods = build_periodization(TODAY, TODAY+timedelta(days=30), [race], transition_days=7)
    transition = next(p for p in periods["phases"] if p["kind"] == "TRANSITION")
    assert transition["days"] == 7
    assert sum(p["days"] for p in periods["phases"]) == 31


def test_skipped_day_is_not_refilled_and_future_race_is_conditional_not_fake_rest():
    repo = Repository()
    skip = (TODAY+timedelta(days=1)).isoformat()
    race_date = (TODAY+timedelta(days=4)).isoformat()
    repo.events = [{"event_type": "CONTROL_RACE", "start_date": race_date, "end_date": race_date}]
    result = engine.generate_plan(repo, "athlete", profile(), start_date=TODAY+timedelta(days=1), now=NOW,
                                  decisions={skip: {"action": "SKIP"}})
    assert result["days"][0]["status"] == "SKIPPED"
    assert result["days"][0]["session"] is None
    assert result["activation_eligible"] is True
    assert all(d["status"] == "REVIEW_REQUIRED" and all(v is None for v in d["readiness_before"].values())
               for d in result["days"] if d["date"] > race_date)
