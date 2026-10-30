from copy import deepcopy
from datetime import timedelta
import math
import pytest

from apps.api import training_plan_engine as engine
from tests.api.test_load_progression import configured, observed
from tests.api.test_readiness_adaptive_plan_v2 import fixed_readiness, assert_rolling_budgets, assert_readiness_dose
from tests.api.test_training_plan_engine import TODAY, NOW, Repository, append_complete_activity


def selected_methods(monkeypatch, body, identifiers):
    methods = [method for method in engine.resolved_methods(body) if method["id"] in identifiers]
    assert len(methods) == len(identifiers)
    for method in methods:
        method["automatic_selection"] = True  # Explicit test selection, including the archived circuit.
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
    repo = Repository()
    plan = engine.generate_plan(repo, "athlete", body, start_date=TODAY, now=NOW)
    assert [segment["days"] for segment in plan["allocation"]["segments"]] == [7,7]
    session = plan["days"][0]["sessions"][0]
    assert session["is_key_session"]
    assert session["method_id"] == "END-THR-TIME-01"
    assert not plan["days"][1]["sessions"], "The two-day key spacing still applies."
    assert_readiness_dose(session)
    assert_rolling_budgets(plan)


@pytest.mark.parametrize("days_ago", [1, 2, 8])
def test_historical_key_before_visible_start_preserves_catchup_and_spacing(monkeypatch, days_ago):
    fixed_readiness(monkeypatch, 100.)
    body = configured(age_years=25, training_experience_years=5, available_minutes=[180]*7)
    body["planning_controls"].update(mesocycle_anchor=(TODAY-timedelta(days=6)).isoformat(),
        sessions_per_week=2, sessions_by_day=[1,1,0,0,0,0,0], threshold_days=[1], threshold_method="INTERVALS")
    selected_methods(monkeypatch, body, {"END-THR-TIME-01"})
    q_calendar(monkeypatch, lambda _: 210.)
    repo = Repository()
    activity = {"activity_ref": "recent-key", "date": (TODAY-timedelta(days=days_ago)).isoformat(),
        "sport": "Run", "duration_min": 10., "zones": [
            {"zone": z, "raw_time_min": 10. if z == "Z3" else 0.,
             "equivalent_time_min": 2. if z == "Z3" else 0.} for z in engine.COMPONENTS if z != "STR"]}
    append_complete_activity(repo.envelope["snapshot_payload"]["load_history"], activity)
    repo.envelope["activities"].append({**deepcopy(activity), "local_date": activity["date"]})
    original = deepcopy(repo.envelope)

    plan = engine.generate_plan(repo, "athlete", body, start_date=TODAY, now=NOW)

    assert plan["status"] == "DRAFT"
    assert repo.envelope == original
    key_dates = [engine.date.fromisoformat(day["date"]) for day in plan["days"]
                 if any(s["is_key_session"] for s in day["sessions"])]
    assert key_dates and all((d-engine.date.fromisoformat(activity["date"])).days >= 2 for d in key_dates)
    if days_ago == 1:
        assert TODAY not in key_dates
    else:
        # Real execution before the visible start must not be mistaken for
        # an unexecuted reservation that suppresses this cycle's catch-up.
        assert TODAY in key_dates
    assert_readiness_dose(next(s for d in plan["days"] for s in d["sessions"] if s["is_key_session"]))
    assert_rolling_budgets(plan)


def test_future_reserved_key_still_suppresses_extra_catchup_in_its_cycle(monkeypatch):
    fixed_readiness(monkeypatch, 100.)
    body = configured(age_years=25, training_experience_years=5, available_minutes=[180]*7,
                      max_key_sessions_per_week=4)
    body["planning_controls"].update(mesocycle_anchor=TODAY.isoformat(),
        sessions_per_week=4, sessions_by_day=[1,1,0,1,0,0,1], threshold_days=[1], threshold_method="INTERVALS")
    selected_methods(monkeypatch, body, {"END-THR-TIME-01"})
    q_calendar(monkeypatch, lambda _: 210.)
    plan = engine.generate_plan(Repository(), "athlete", body, start_date=TODAY, now=NOW)
    key_dates = [day["date"] for day in plan["days"] if any(s["is_key_session"] for s in day["sessions"])]
    assert key_dates == [(TODAY+timedelta(days=1)).isoformat()]


def test_second_slot_can_use_a_reduced_mixed_dose_after_strength(monkeypatch):
    from tests.api.test_methodology_completion import proven_shortage
    proven_shortage(monkeypatch)
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


def test_interval_maintenance_nominal_preserves_whole_efforts_without_generic_floor():
    body = configured(age_years=25, training_experience_years=5)
    method = next(method for method in engine.resolved_methods(body) if method["id"] == "ONFLOWS-CONTROLLED-Z4-V2")
    evidence = {"capacity_minutes":34., "target_hr_bpm":None, "target_speed_kmh":None,
                "effort_profile":deepcopy(method["interval_template"]), "readiness_dose_factor":.9957}
    fraction = engine._nominal_fraction(method, "MAINTENANCE", body, "GENERAL_PREPARATION", evidence, None)
    work = math.floor(evidence["capacity_minutes"]*fraction*evidence["readiness_dose_factor"]*2)/2
    minimum = engine._minimum_work(method, evidence, Repository().settings)
    blocks = engine._blocks(method, work, evidence, Repository().settings)
    assert minimum == 3.  # One whole native effort; no generic Tmax floor.
    assert fraction == .78  # 60% of the developmental method dose.
    assert work >= minimum
    assert [block["duration_min"] for block in blocks if block["kind"] == "WORK"] == [3.]*8
    assert engine._dose_usage(blocks, evidence, "Z4") <= .5*evidence["readiness_dose_factor"]
    assert fraction <= method["interval_template"]["total_capacity_ratio"]
