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
    # A 40-minute E target is below the new complete relative minimum.
    assert plan["summary"]["sessions"] == 0
    assert any(r["code"] in {"COMPONENT_BUDGET_EXHAUSTED", "PERIOD_COMPONENT_REMAINDER", "INSUFFICIENT_DOSE_BUDGET"} for d in plan["days"] for r in d["rejected_alternatives"])
    # Available slots never force a subminimum dose, including across a wave change.
    for d in plan["days"]:
        if all(v <= .001 for v in d["load_budget"]["component_allocation"].values()):
            assert not d["sessions"]


@pytest.mark.parametrize("target", [180., 250., 400.])
def test_feasible_volume_is_realized_in_complete_sessions(monkeypatch, target):
    p = body(sessions_per_week=7, accent_mode="MANUAL", accents=["Z1"], mesocycle_anchor=TODAY)
    p["component_targets_weekly"] = {z: target if z == "Z1" else 0. for z in COMPONENTS}
    p["max_key_sessions_per_week"] = 0
    plan = run(monkeypatch, p)
    row = plan["allocation"]["components"]["Z1"]
    assert row["remaining"] < .5  # only the half-minute dose grid remains
    assert row["planned"] <= row["target"] + .005
    selected = [s for d in plan["days"] for s in d["sessions"]]
    assert 1 < len(selected) < 7
    assert all(s["dose_evidence"]["applied_structure_fraction"] >= .25 for s in selected)


def test_period_objective_integrates_wave_and_counts_q_without_cascade():
    windows = {TODAY+timedelta(days=i): {z: {"target": 700., "target_weekly_q": 140. if i<2 else 70.}
                                         for z in COMPONENTS} for i in range(4)}
    actual = [{"date": d.isoformat(), "zone": z, "effective_load": 50.}
              for d in [TODAY-timedelta(days=1), TODAY] for z in COMPONENTS]
    source = {"activities": [{"date": TODAY.isoformat(), "zones": [{"zone": z, "equivalent_time_min": 10.} for z in COMPONENTS]}]}
    session = {"direct_equivalent_minutes": {z: 5. for z in COMPONENTS}}
    days = [{"date": TODAY.isoformat(), "sessions": [session]}]
    forecast = actual + [{"date": (TODAY+timedelta(days=1)).isoformat(), "zone": z, "effective_load": 100.} for z in COMPONENTS]
    obj = planning_allocation.objectives(windows, actual, forecast, source, days)
    z1 = obj["Z1"]
    assert z1["target"] == 60.  # 2*140/7 + 2*70/7; neither last-day nor full-week target
    assert z1["actual"] == 10. and z1["planned"] == 5. and z1["remaining"] == 45.
    assert z1["planned_effective"] == 100.  # E cascade cannot fill the Q objective
    assert obj["STR"]["basis"] == "DIRECT_Q" and obj["STR"]["target"] == 400.
    slots = {z: [(TODAY,0), (TODAY,1)] for z in COMPONENTS}
    assert planning_allocation.quota(windows, slots, forecast, TODAY, 0, objectives=obj)["Z1"] == 45.
    assert planning_allocation.dose_share(180., 50., 100., 12) == 90.
    assert planning_allocation.dose_share(40., 50., 100., 12) is None
    assert planning_allocation.dose_share(180., 50., 100., 1) is None


def test_automatic_q_targets_match_outlook_and_no_micro_sessions(monkeypatch):
    from tests.api.test_load_progression import observed
    from apps.api.management_schemas import LoadProgression
    repo, _, _ = observed()
    p = body(sessions_per_week=12, accent_mode="MANUAL", accents=["Z3"], mesocycle_anchor=TODAY)
    p["load_progression"] = LoadProgression().model_dump()
    plan = run(monkeypatch, p, repo)
    for z in COMPONENTS:
        objective = plan["allocation"]["components"][z]
        assert objective["target_q"] == pytest.approx(plan["long_term"]["weeks"][0]["components"][z]["target_period_q"], abs=.001)
        assert objective["planned_q"] == pytest.approx(sum(s["direct_equivalent_minutes"][z] for d in plan["days"] for s in d["sessions"]), abs=.001)
    selected = [s for d in plan["days"] for s in d["sessions"]]
    assert selected
    assert all(s["dose_evidence"]["applied_structure_fraction"] >= .25 for s in selected if s["zone"] != "STR")
    assert all(s["total_minutes"] > 10 for s in selected if s["zone"] == "Z1")
    # Unavailable Z5 model and disabled strength remain visible, not silently covered by spill.
    assert plan["allocation"]["components"]["Z5"]["remaining"] > 0
    assert plan["allocation"]["has_unallocated_load"]


def test_feasible_automatic_q_target_is_realized_without_filling_e_headroom(monkeypatch):
    from tests.api.test_management_schedule import high_capacity_history
    from apps.api.management_schemas import LoadProgression
    repo = high_capacity_history()
    source = repo.envelope["snapshot_payload"]["load_history"]
    for row in source["daily"] + source["strength"]["daily"]:
        if row.get("zone", "STR") != "Z1":
            row["effective_load"] = 0.
    for a in source["activities"]:
        a["zones"] = [{"zone": "Z1", "raw_time_min": 60., "equivalent_time_min": 50.}]
    p = body(sessions_per_week=7, accent_mode="MANUAL", accents=["Z1"], mesocycle_anchor=TODAY)
    p["load_progression"] = LoadProgression().model_dump()
    p["max_key_sessions_per_week"] = 0
    plan = run(monkeypatch, p, repo)
    row = plan["allocation"]["components"]["Z1"]
    assert row["basis"] == "DIRECT_Q"
    assert 0 <= row["remaining"] < .5
    assert row["target_q"] == plan["long_term"]["weeks"][0]["components"]["Z1"]["target_period_q"]
    assert row["unallocated_effective"] > 1000  # a separate ceiling, not missing direct volume
    assert not plan["allocation"]["has_unallocated_load"]


def test_missing_actual_q_is_unknown_not_zero_coverage():
    windows = {TODAY+timedelta(days=i): {z: {"target": 700., "target_weekly_q": 140.} for z in COMPONENTS} for i in range(7)}
    source = {"activities": [{"date": TODAY.isoformat(), "zones": []}]}
    row = planning_allocation.objectives(windows, [], [], source, [])["Z1"]
    assert row["actual"] is None and row["actual_q"] is None and row["remaining"] is None
