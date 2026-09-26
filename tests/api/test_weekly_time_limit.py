"""Time ceilings are totals including actual activity, never extra hours."""
from copy import deepcopy
from datetime import timedelta

import pytest

from apps.api import training_plan_engine as engine
from tests.api.test_management_schedule import body, high_capacity_history, run
from tests.api.test_training_plan_engine import TODAY, NOW, reference_speed


def test_one_hour_limit_consumed_by_actual_sessions_is_explicit_and_removable(monkeypatch):
    repo = high_capacity_history()
    source = repo.envelope["snapshot_payload"]["load_history"]
    for i, duration in enumerate((45, 30)):
        actual = {"activity_ref": f"today-{i}", "date": TODAY.isoformat(), "sport": "Run", "duration_min": duration, "zones": []}
        source["activities"].append(actual)
        repo.envelope["activities"].append({**actual, "local_date": actual["date"]})
    original = deepcopy(repo.envelope)
    p = body(weekly_target_hours=1)
    p["max_key_sessions_per_week"] = 0
    limited = run(monkeypatch, p, repo)
    budget = limited["parameters"]["time_budget"]
    assert budget == {"start_date": TODAY.isoformat(), "end_date": (TODAY+timedelta(days=6)).isoformat(),
                      "period_limit_minutes": 60, "actual_minutes": 75, "planned_minutes": 0,
                      "remaining_minutes": 0, "actual_excess_minutes": 15}
    assert limited["summary"]["sessions"] == 0
    assert limited["summary"]["actual_sessions"] == 2
    assert limited["days"][0]["status"] == "EXISTING_ACTIVITY"
    blocked = [d for d in limited["days"] if d.get("time_limit_exhausted")]
    assert blocked
    assert all(d["status"] == "UNAVAILABLE" and "Лимитът за общо време" in d["explanation"] for d in blocked)
    assert limited["parameters"]["weekly_time_limit_minutes"] == 60
    p["planning_controls"]["weekly_target_hours"] = None
    restored = run(monkeypatch, p, repo)
    assert restored["summary"]["sessions"] > 0
    assert restored["parameters"]["time_budget"] is None
    assert repo.envelope == original


@pytest.mark.parametrize("hours, fits", [(1., False), (1.5, True)])
def test_small_limit_preserves_only_complete_minimum_doses(monkeypatch, hours, fits):
    p = body(weekly_target_hours=hours, sessions_per_week=7, intensity_days=[], strength_days=[])
    p["max_key_sessions_per_week"] = 0
    plan = run(monkeypatch, p)
    assert (plan["summary"]["planned_minutes"] > 0) is fits
    assert plan["summary"]["planned_minutes"] <= hours * 60
    if not fits:
        assert any(r["code"] == "INSUFFICIENT_TIME_BUDGET" for d in plan["days"] for r in d["rejected_alternatives"])
    budget = plan["parameters"]["time_budget"]
    assert budget["actual_minutes"] == 0
    assert budget["planned_minutes"] + budget["remaining_minutes"] == pytest.approx(hours * 60, abs=.002)


def test_short_period_prorates_total_ceiling_and_excludes_past_actuals(monkeypatch):
    monkeypatch.setattr(engine.model_service, "speed_view", reference_speed)
    p = body(weekly_target_hours=7)
    p.update(horizon_mode="MANUAL", program_end=(TODAY+timedelta(days=2)).isoformat())
    plan = engine.generate_plan(high_capacity_history(), "athlete", p, start_date=TODAY+timedelta(days=1), now=NOW)
    budget = plan["parameters"]["time_budget"]
    assert plan["parameters"]["weekly_time_limit_minutes"] == 420
    assert budget["period_limit_minutes"] == 120
    assert budget["actual_minutes"] == 0
    assert budget["planned_minutes"] <= 120


def test_daily_availability_and_weekly_limit_both_apply(monkeypatch):
    p = body(weekly_target_hours=10)
    p.update(availability_mode="MANUAL", available_minutes=[30]*7)
    plan = run(monkeypatch, p)
    assert plan["parameters"]["weekly_time_limit_minutes"] == 600
    assert plan["parameters"]["available_weekly_minutes"] == 210
    assert plan["parameters"]["time_budget"]["period_limit_minutes"] == 210
    assert plan["summary"]["planned_minutes"] <= 210
    assert all(sum(s["total_minutes"] for s in d["sessions"]) <= 30 for d in plan["days"])
