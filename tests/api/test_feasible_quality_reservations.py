from copy import deepcopy
from datetime import timedelta
import math

from apps.api import training_plan_engine as engine
from tests.api.test_load_progression import configured, observed
from tests.api.test_readiness_adaptive_plan_v2 import fixed_readiness, assert_rolling_budgets, assert_readiness_dose
from tests.api.test_training_plan_engine import TODAY, NOW, Repository


def selected_methods(monkeypatch, body, identifiers):
    methods = [method for method in engine.resolved_methods(body) if method["id"] in identifiers]
    assert len(methods) == len(identifiers)
    monkeypatch.setattr(engine, "resolved_methods", lambda _: deepcopy(methods))


def q_calendar(monkeypatch, target):
    original = engine._goals
    def goals(body, day, *args, **kwargs):
        values, accents, cycle = original(body, day, *args, **kwargs)
        values["Z3"]["target_weekly_q"] = target(day)
        values["Z3"]["desired_weekly_q"] = target(day)
        return values, accents, cycle
    monkeypatch.setattr(engine, "_goals", goals)


def test_future_reduced_q_target_cannot_cancel_a_legal_due_key_minimum(monkeypatch):
    fixed_readiness(monkeypatch, 100.)
    body = configured(age_years=25, training_experience_years=5, available_minutes=[180]*7)
    body["planning_controls"].update(sessions_per_week=2, sessions_by_day=[0,1,0,0,1,0,0], threshold_days=[1,4], threshold_method="INTERVALS")
    selected_methods(monkeypatch, body, {"END-THR-TIME-01"})
    friday = TODAY+timedelta(days=4)
    q_calendar(monkeypatch, lambda day: 80. if day < friday else 20.)
    repo, _, _ = observed()
    plan = engine.generate_plan(repo, "athlete", body, start_date=TODAY, now=NOW)
    tuesday = next(day for day in plan["days"] if day["date"] == (TODAY+timedelta(days=1)).isoformat())
    assert len(tuesday["sessions"]) == 1
    session = tuesday["sessions"][0]
    assert session["is_key_session"]
    # The frozen long-term microcycle is distributed across both dates. A
    # later smaller rolling level is no longer a second prescription target.
    assert session["dose_evidence"]["primary_work_budget_minutes"] >= session["dose_evidence"]["minimum_primary_work_minutes"]
    for segment in plan["allocation"]["segments"]:
        assert not segment["components"]["Z3"]["planned_exceeds_target"]
    assert_readiness_dose(session)
    assert_rolling_budgets(plan)


def test_reserved_quality_slot_is_used_for_executable_quality_before_optional_mixed_work(monkeypatch):
    fixed_readiness(monkeypatch, 100.)
    body = configured(age_years=25, training_experience_years=5, available_minutes=[180]*7)
    body["planning_controls"].update(sessions_per_week=1, sessions_by_day=[0,0,1,0,0,0,0], threshold_days=[2], threshold_method="INTERVALS", mixed_sessions_enabled=True)
    selected_methods(monkeypatch, body, {"END-THR-TIME-01", "END-CROSS-TRAIN-01-Z2-MIX-STEADY"})
    q_calendar(monkeypatch, lambda _: 34.)
    repo, _, _ = observed()
    plan = engine.generate_plan(repo, "athlete", body, start_date=TODAY, now=NOW)
    sessions = [session for day in plan["days"] for session in day["sessions"]]
    assert len(sessions) == 1
    assert sessions[0]["is_key_session"]
    assert sessions[0]["method_id"] == "END-THR-TIME-01"
    assert_readiness_dose(sessions[0])
    assert_rolling_budgets(plan)


def test_last_microcycle_day_does_not_wait_for_next_microcycle_preferred_threshold(monkeypatch):
    fixed_readiness(monkeypatch, 100.)
    body = configured(age_years=25, training_experience_years=5, available_minutes=[180]*7)
    body["planning_controls"].update(mesocycle_anchor=(TODAY-timedelta(days=6)).isoformat(),
        sessions_per_week=2, sessions_by_day=[1,1,0,0,0,0,0], threshold_days=[1], threshold_method="INTERVALS")
    selected_methods(monkeypatch, body, {"END-THR-TIME-01"})
    q_calendar(monkeypatch, lambda _:210.)
    repo, _, _ = observed()
    plan = engine.generate_plan(repo, "athlete", body, start_date=TODAY, now=NOW)
    assert [segment["days"] for segment in plan["allocation"]["segments"]] == [1,6]
    session = plan["days"][0]["sessions"][0]
    assert session["is_key_session"]
    assert session["method_id"] == "END-THR-TIME-01"
    assert not plan["days"][1]["sessions"], "The two-day key spacing still applies."
    assert_readiness_dose(session)
    assert_rolling_budgets(plan)


def test_second_slot_can_use_a_reduced_mixed_dose_after_strength(monkeypatch):
    fixed_readiness(monkeypatch, 100.)
    body = configured(age_years=25, training_experience_years=5, strength_enabled=True, available_minutes=[180]*7)
    body["planning_controls"].update(sessions_per_week=2, sessions_by_day=[2,0,0,0,0,0,0], strength_days=[0], threshold_days=[4], mixed_sessions_enabled=True)
    selected_methods(monkeypatch, body, {"STR-CIRCUIT-RUN-01", "END-THR-TIME-01-FLEX-MIX-STEADY"})
    repo, _, _ = observed()
    plan = engine.generate_plan(repo, "athlete", body, start_date=TODAY, now=NOW)
    sessions = plan["days"][0]["sessions"]
    assert len(sessions) == 2
    assert sessions[0]["zone"] == "STR"
    assert sessions[1]["mixed_component"] and sessions[1]["purpose"] == "SUPPORTING"
    assert not sessions[1]["is_key_session"]
    assert_readiness_dose(sessions[1])
    assert_rolling_budgets(plan)


def test_interval_maintenance_nominal_retains_a_complete_relative_minimum_after_readiness_scale():
    body = configured(age_years=25, training_experience_years=5)
    method = next(method for method in engine.resolved_methods(body) if method["id"] == "ONFLOWS-CONTROLLED-Z4-V2")
    evidence = {"capacity_minutes":34., "target_hr_bpm":None, "target_speed_kmh":None,
                "effort_profile":deepcopy(method["interval_template"]), "readiness_dose_factor":.9957}
    fraction = engine._nominal_fraction(method, "MAINTENANCE", body, "GENERAL_PREPARATION", evidence, None)
    work = math.floor(evidence["capacity_minutes"]*fraction*evidence["readiness_dose_factor"]*2)/2
    minimum = engine._minimum_work(method, evidence, Repository().settings)
    blocks = engine._blocks(method, work, evidence, Repository().settings)
    assert minimum == 9.
    assert fraction == 12./34.  # Three mandatory whole repetitions, plus one before readiness.
    assert work >= minimum
    assert [block["duration_min"] for block in blocks if block["kind"] == "WORK"] == [3.]*3
    assert engine._dose_usage(blocks, evidence, "Z4") <= body["maintenance_fraction"]*evidence["readiness_dose_factor"]
    assert fraction <= method["interval_template"]["total_capacity_ratio"]
