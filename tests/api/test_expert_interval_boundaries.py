"""Unavailable expert curve points reject candidates without aborting a plan."""
from copy import deepcopy

import pytest

from apps.api import training_plan_engine as engine
from apps.api.management_schemas import IntervalDoseProfile
from tests.api.test_expert_method_dosing import interval_method
from tests.api.test_method_curve_capacity import SETTINGS, single_anchor_context
from tests.api.test_training_plan_engine import TODAY, NOW, Repository, supported_speed


def curve_context(*, blended=False):
    speed, context = single_anchor_context(blended=blended)
    speed.update(active_test_keys=["anchor"],
                 tests=[{"entry_key": "anchor", "payload": context[1][0]}],
                 hr_model=context[0].summary())
    return speed, context


@pytest.mark.parametrize("blended", [False, True])
@pytest.mark.parametrize("coefficient", [2000., 1e308])
def test_valid_expert_profile_outside_curve_domain_rejects_without_fallback(blended, coefficient):
    method = interval_method()
    method["interval_profile"]["speed_time_duration_ratio"] = coefficient
    # The expert coefficient has no arbitrary ceiling. Its resulting curve
    # point can nonetheless be unsupported or overflow to infinity.
    validated = IntervalDoseProfile.model_validate(method["interval_profile"])
    assert validated.speed_time_duration_ratio == coefficient
    before = deepcopy(method)
    speed, context = curve_context(blended=blended)

    assert engine.capacity_for(method, SETTINGS, speed, context, TODAY,
                               use_model_prior=True) is None
    assert method == before


def test_observed_only_rejection_does_not_use_available_coach_fallback():
    method = interval_method()
    speed, context = curve_context()
    # A fresh explicit assessment is available, but cannot bypass the chosen
    # evidence policy for an existing individual curve.
    assert engine.capacity_for(method, SETTINGS, speed, context, TODAY) is None
    allowed = engine.capacity_for(method, SETTINGS, speed, context, TODAY,
                                  use_model_prior=True)
    assert allowed["capacity_minutes"] == pytest.approx(80 / 60)
    assert allowed["capacity_reference"] == "EXPERT_REPETITION_DURATION_COEFFICIENT_ON_CURVE"


def test_resolved_curve_budget_overflow_rejects_despite_finite_coach_fallback():
    method = interval_method()
    method["interval_profile"].update(continuous_capacity_min=.8, total_capacity_ratio=3e306)
    # Fallback: 48s × ratio is finite. Individual curve: 80s × ratio is not.
    IntervalDoseProfile.model_validate(method["interval_profile"])
    speed, context = curve_context()

    assert engine.capacity_for(method, SETTINGS, speed, context, TODAY,
                               use_model_prior=True) is None


@pytest.mark.parametrize("coefficient", [None, 2., 1e308])
def test_no_curve_keeps_explicit_assessment_without_manufacturing_speed(coefficient):
    method = interval_method()
    method["interval_profile"]["speed_time_duration_ratio"] = coefficient
    before = deepcopy(method)

    evidence = engine.capacity_for(method, SETTINGS, None, (None, [], []), TODAY,
                                   use_model_prior=True)

    assert evidence["capacity_source"] == "COACH_EFFORT_CAPACITY"
    assert evidence["capacity_minutes"] == method["interval_profile"]["continuous_capacity_min"]
    assert evidence["target_speed_kmh"] is None
    assert method == before


def test_planner_continues_after_expert_curve_point_is_unavailable(monkeypatch):
    from tests.api.test_load_progression import configured

    repo = Repository()
    speed = supported_speed(repo.settings)
    speed.update(sport="Run", source_generation_id="generation-one", source_revision=1)
    body = configured(age_years=30, training_experience_years=5,
                      interval_profiles=[interval_method()["interval_profile"]])
    body["interval_profiles"][0]["speed_time_duration_ratio"] = 2000.
    body["planning_controls"]["accents"] = ["Z5"]
    monkeypatch.setattr(engine.model_service, "speed_view", lambda *args, **kwargs: deepcopy(speed))

    plan = engine.generate_plan(repo, "athlete", body, start_date=TODAY, now=NOW)

    assert len(plan["days"]) == 7
    assert any(day["sessions"] for day in plan["days"])
    assert any(rejection["code"] == "CAPACITY_UNAVAILABLE"
               and "END-VO2-TREF-01-Z5" in rejection.get("method_ids", [rejection.get("method_id")])
               for day in plan["days"] for rejection in day["rejected_alternatives"])
    assert all(session["zone"] != "Z5" for day in plan["days"] for session in day["sessions"])


def test_mixed_method_retains_independent_primary_capacity_with_interval_coefficient():
    from tests.api.test_load_progression import configured

    repo = Repository()
    speed = supported_speed(repo.settings)
    speed["sport"] = "Run"
    context = engine._capacity_context(speed, repo.settings)
    body = configured(age_years=30, training_experience_years=5,
                      interval_profiles=[interval_method()["interval_profile"]])
    method = next(m for m in engine.resolved_methods(body) if m["structure"] == "THRESHOLD_HIGH")
    before = deepcopy(method)
    ordinary_primary = {**method, "structure": "CONTINUOUS"}

    primary = engine.capacity_for(method, repo.settings, speed, context, TODAY, use_model_prior=True)
    expected = engine.capacity_for(ordinary_primary, repo.settings, speed, context, TODAY, use_model_prior=True)

    assert primary is not None
    assert primary == expected
    assert "effort_profile" not in primary
    assert method == before


@pytest.mark.parametrize("policy,readiness,expected_sessions", [
    ("MODEL_WITH_PRIOR", 100., 1),
    ("MODEL_WITH_PRIOR", 80., 1),
    ("OBSERVED_ONLY", 100., 0),
    ("MODEL_WITH_PRIOR", 0., 0),
])
def test_mixed_reservation_and_prescription_share_curve_policy_and_single_recovery(
        monkeypatch, policy, readiness, expected_sessions):
    from biathlon import hr_speed, speed_duration
    from tests.api.test_load_progression import configured
    from tests.api.test_readiness_adaptive_plan_v2 import fixed_readiness

    repo = Repository()
    speed = supported_speed(repo.settings)
    curve = speed_duration.calibrated([entry["payload"] for entry in speed["tests"]])
    # Put the measured Z3 index at the lower expert duration bound. The
    # composite method's existing 12-minute ceiling then admits a whole dose.
    tmax_seconds = hr_speed.TMAX_RANGES_S["Z3"][0]
    speed["index_summary"]["Z3"]["index"] = (
        100 * repo.settings.zone_bounds_bpm[3] / repo.settings.hrmax_bpm
        / (curve.speed(tmax_seconds) * 3.6))
    speed.update(sport="Run", source_generation_id="generation-one", source_revision=1)
    speed["hr_model"] = engine._capacity_context(speed, repo.settings)[0].summary()
    body = configured(age_years=30, training_experience_years=5,
                      interval_profiles=[interval_method()["interval_profile"]],
                      available_minutes=[180] * 7)
    body["planning_controls"].update(capacity_policy=policy, accents=["Z3"], sessions_per_week=1,
        sessions_by_day=[0, 1, 0, 0, 0, 0, 0], threshold_days=[1])
    method = next(m for m in engine.resolved_methods(body) if m["structure"] == "THRESHOLD_HIGH")
    method.update(position=1., periods=("GENERAL_PREPARATION", "SPECIAL_PREPARATION", "PRECOMPETITION"))
    fixed_readiness(monkeypatch, readiness)
    monkeypatch.setattr(engine, "resolved_methods", lambda _: [deepcopy(method)])
    monkeypatch.setattr(engine.model_service, "speed_view", lambda *args, **kwargs: deepcopy(speed))

    plan = engine.generate_plan(repo, "athlete", body, start_date=TODAY, now=NOW)

    sessions = [session for day in plan["days"] for session in day["sessions"]]
    assert len(sessions) == expected_sessions
    if not sessions:
        return
    evidence = sessions[0]["dose_evidence"]
    factor = readiness / 100
    assert evidence["capacity_minutes"] == pytest.approx(tmax_seconds / 60)
    assert evidence["primary_requested_work"] == pytest.approx(tmax_seconds / 60 * .65 * .5 * factor)
    assert evidence["combination_high_work_cap"] == pytest.approx(80 / 60 * 3 * .5 * factor)
    secondary = evidence["secondary_capacity"]
    assert secondary["capacity_reference"] == "EXPERT_REPETITION_DURATION_COEFFICIENT_ON_CURVE"
    assert secondary["capacity_minutes"] == pytest.approx(80 / 60)
    high_blocks = [block for block in sessions[0]["blocks"] if block["kind"] == "WORK" and block["zone"] == "Z5"]
    assert high_blocks
    assert all(block["target_speed_kmh"] == pytest.approx(curve.speed(80) * 3.6, abs=.001)
               for block in high_blocks)
