"""Integration: split imports must not consume the whole weekly/day quota."""
from copy import deepcopy
from datetime import date, timedelta

import pytest

from apps.api import training_plan_engine as engine
from tests.api.test_management_schedule import body, high_capacity_history, completed_activity, stored_activity_load, run
from tests.api.test_training_plan_engine import TODAY, NOW


def split_session(repo, starts=None):
    starts = starts or ["11:02:00", "11:44:00", "11:49:00", "11:55:00", "12:00:00", "12:06:00", "12:11:00"]
    minutes = [10+56/60, 4, 31/60, 3+58/60, .5, 3.9, 28/60]
    for i, (start, duration) in enumerate(zip(starts, minutes)):
        completed_activity(repo, f"split_{i}", minutes=duration)
        repo.envelope["activities"][-1].update(start_local=f"{TODAY}T{start}", elapsed_time_s=duration*60)


def test_seven_imports_leave_weekly_headroom_without_changing_recovery_or_load(monkeypatch):
    repo = high_capacity_history()
    split_session(repo)
    original = deepcopy(repo.envelope)
    p = body(sessions_per_week=7)
    p["max_key_sessions_per_week"] = 0
    # Establish the speed fixture before generating tomorrow's draft.
    from tests.api.test_training_plan_engine import reference_speed
    monkeypatch.setattr(engine.model_service, "speed_view", reference_speed)
    plan = engine.generate_plan(repo, "athlete", p, start_date=TODAY+timedelta(days=1), now=NOW)
    assert plan["days"][0]["sessions"]
    for day in plan["days"]:
        end = date.fromisoformat(day["date"])
        start = end - timedelta(days=6)
        recorded_count = int(start <= TODAY <= end)
        planned_count = sum(
            len(row["sessions"]) for row in plan["days"]
            if start <= date.fromisoformat(row["date"]) <= end
        )
        assert recorded_count + planned_count <= 7
    assert repo.envelope == original
    for row in repo.envelope["activities"]:
        row.pop("start_local", None)
    separate = engine.generate_plan(repo, "athlete", p, start_date=TODAY+timedelta(days=1), now=NOW)
    assert not separate["days"][0]["sessions"]
    assert "максимумът" in separate["days"][0]["explanation"]
    assert plan["days"][0]["readiness_before"] == separate["days"][0]["readiness_before"]


@pytest.mark.parametrize("separate_afternoon", [False, True])
def test_daily_slots_count_occasions_and_keep_all_recorded_minutes(monkeypatch, separate_afternoon):
    repo = high_capacity_history()
    split_session(repo)
    if separate_afternoon:
        completed_activity(repo, "afternoon", minutes=30)
        repo.envelope["activities"][-1].update(start_local=f"{TODAY}T17:00:00", elapsed_time_s=1800)
    original = deepcopy(repo.envelope)
    p = body(sessions_per_week=13, sessions_by_day=[2,2,2,2,2,2,1])
    p["max_key_sessions_per_week"] = 0
    plan = run(monkeypatch, p, repo)
    today = plan["days"][0]
    assert len(today["sessions"]) == (0 if separate_afternoon else 1)
    assert plan["summary"]["actual_sessions"] == (2 if separate_afternoon else 1)
    assert plan["summary"]["actual_activity_records"] == (8 if separate_afternoon else 7)
    assert plan["summary"]["actual_minutes"] == pytest.approx(24+17/60+(30 if separate_afternoon else 0), abs=.002)
    assert repo.envelope == original
    if not separate_afternoon:
        preserved = engine.generate_plan(repo, "athlete", p, start_date=TODAY, now=NOW, locked_day=today)
        assert preserved["days"][0]["locked"]
        assert preserved["days"][0]["sessions"] == today["sessions"]


def test_split_intervals_still_consume_one_key_quota(monkeypatch):
    repo = high_capacity_history()
    for i in range(2):
        completed_activity(repo, f"interval_{i}", minutes=3.)
        repo.envelope["activities"][-1].update(start_local=f"{TODAY}T08:{i*5:02}:00", elapsed_time_s=180.)
    source = repo.envelope["snapshot_payload"]["load_history"]
    for activities in (repo.envelope["activities"], source["activities"]):
        for activity in activities:
            if not activity["activity_ref"].startswith("interval_"):
                continue
            for zone in activity["zones"]:
                zone.update(raw_time_min=3. if zone["zone"] == "Z3" else 0.,
                            equivalent_time_min=1.5 if zone["zone"] == "Z3" else 0.)
            stored_activity_load(activity)
    for row in source["daily"]:
        if row["date"] == TODAY.isoformat():
            row["effective_load"] = sum(zone["effective_load"] for activity in source["activities"]
                if activity["date"] == row["date"] for zone in activity["zones"] if zone["zone"] == row["zone"])
    p = body(sessions_per_week=13, sessions_by_day=[2]*6+[1], threshold_days=[TODAY.weekday()])
    p["max_key_sessions_per_week"] = 1
    plan = run(monkeypatch, p, repo)
    assert plan["summary"]["actual_sessions"] == 1
    assert plan["summary"]["sessions"] > 0
    assert not any(s["is_key_session"] for day in plan["days"] for s in day["sessions"])
