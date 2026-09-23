"""Rolling allocation, exposure limits and dose rules must remain independent."""
from copy import deepcopy
from datetime import timedelta

import pytest

from biathlon import planning_allocation, planning_schedule
from biathlon.constants import COMPONENTS
from tests.api.test_management_schedule import body, run
from tests.api.test_training_plan_engine import Repository, TODAY


def test_quota_reallocates_canonical_load_and_drops_expired_actuals():
    end = TODAY+timedelta(days=6)
    targets = {z: {"target": 210.} for z in COMPONENTS}
    windows = {TODAY: targets, end: targets}
    opportunities = {z: [(TODAY,0),(TODAY+timedelta(days=2),0),(end,0)] for z in COMPONENTS}
    old = [{"date": (TODAY-timedelta(days=5)).isoformat(), "zone": z, "effective_load": 60.} for z in COMPONENTS]
    q = planning_allocation.quota(windows, opportunities, old, TODAY, 0)
    assert q == {z: 70. for z in COMPONENTS}  # no forced catch-up of today's 150 deficit
    forecast = old+[{"date": TODAY.isoformat(), "zone": z, "effective_load": 70.} for z in COMPONENTS]
    assert planning_allocation.quota(windows, opportunities, forecast, end, 0) == {z:140. for z in COMPONENTS}


def test_future_build_target_cannot_fill_today_taper_budget():
    future = TODAY+timedelta(days=1)
    windows = {TODAY:{z:{"target":10.} for z in COMPONENTS}, future:{z:{"target":210.} for z in COMPONENTS}}
    slots = {z:[(TODAY,0),(future,0)] for z in COMPONENTS}
    assert planning_allocation.quota(windows, slots, [], TODAY, 0) == {z:10. for z in COMPONENTS}
    assert planning_allocation.coverage({z:200. for z in COMPONENTS}, {z:10. for z in COMPONENTS}, []) == 6.


def test_nonaccent_endurance_uses_weekly_need_without_raising_coach_fraction(monkeypatch):
    repo = Repository()
    source = repo.envelope["snapshot_payload"]["load_history"]
    for a in source["activities"]:
        a["duration_min"] = 180.
    for row in source["daily"]:
        if row["effective_load"] and row["zone"] == "Z1":
            row["effective_load"] = 140.
    p = body(sessions_per_week=9, sessions_by_day=[2,1,1,2,1,2,0],
             accent_mode="MANUAL", accents=["Z4","Z5"], wave=[1.,1.,1.,.78])
    p["max_key_sessions_per_week"] = 0
    original = deepcopy(p)
    plan = run(monkeypatch, p, repo)
    selected = [s for d in plan["days"] for s in planning_schedule.day_sessions(d)]
    building = [s for s in selected if s["dose_evidence"]["selection"].get("endurance_dose_from_weekly_need")]
    assert building
    assert any(s["purpose"] == "BUILDING" and s["main_work_minutes"] > s["dose_evidence"]["capacity_minutes"]*.3 for s in building)
    assert all(s["dose_evidence"]["applied_fraction"] <= .501 for s in selected if s["zone"] in {"Z1","Z2"})
    assert p == original
    assert plan["allocation"]["scheduled_slots"] == 9
    assert plan["allocation"]["requires_catchup"] is False
    for day in plan["days"]:
        for z in COMPONENTS:
            total = sum(s["canonical_effective_load"][z] for s in day["sessions"])
            assert total <= day["load_budget"]["components"][z]["deficit_effective"] + .01
    for z, row in plan["allocation"]["components"].items():
        assert row["planned_effective"] == pytest.approx(sum(s["canonical_effective_load"][z] for s in selected), abs=.01)
        assert row["unallocated_effective"] == pytest.approx(max(0., row["target_effective"]-row["actual_effective"]-row["planned_effective"]), abs=.01)


def test_low_manual_goal_still_blocks_and_reports_shortfall(monkeypatch):
    p = body(sessions_per_week=13, sessions_by_day=[2,2,1,1,2,1,0])
    p["component_targets_weekly"] = {"Z1":5}
    plan = run(monkeypatch, p)
    report = plan["allocation"]
    assert report["weekly_session_limit"] == 13 and report["scheduled_slots"] == 9
    assert report["has_unallocated_load"]
    assert any(r["code"] == "COMPONENT_BUDGET_EXHAUSTED" for r in report["constraints"])
    z1 = report["components"]["Z1"]
    assert z1["planned_effective"] <= z1["target_effective"]+.01
    assert p["component_targets_weekly"] == {"Z1":5}


def test_lower_future_target_does_not_force_filling_available_sessions(monkeypatch):
    p = body(sessions_per_week=21, sessions_by_day=[3]*7,
             mesocycle_anchor=(TODAY-timedelta(days=5)).isoformat(), wave=[1.,.5,1.,.5])
    p["component_targets_weekly"] = {z:40. if z == "Z1" else 0. for z in COMPONENTS}
    p["max_key_sessions_per_week"] = 0
    plan = run(monkeypatch, p, Repository())
    assert plan["summary"]["sessions"] > 0
    assert any(r["code"] == "WEEKLY_NEED_COVERED" for d in plan["days"] for r in d["rejected_alternatives"])
    # Once the end-window need is covered, later available slots stay empty,
    # even if the earlier calendar-day ceiling still has headroom.
    for d in plan["days"]:
        if all(v <= .001 for v in d["load_budget"]["component_allocation"].values()):
            assert not d["sessions"]
