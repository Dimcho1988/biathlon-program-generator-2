"""Rolling allocation, exposure limits and dose rules must remain independent."""
from copy import deepcopy
from datetime import timedelta

import pytest

from biathlon import planning_allocation, planning_schedule
from biathlon.constants import COMPONENTS
from tests.api.test_management_schedule import body, run
from tests.api.test_training_plan_engine import Repository, TODAY
from tests.api.test_readiness_adaptive_plan_v2 import assert_readiness_dose


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


def test_coverage_fixed_target_does_not_magnify_small_remaining_allocation():
    load = {z: 0. for z in COMPONENTS}
    allocation = {z: 0. for z in COMPONENTS}
    targets = {z: 100. for z in COMPONENTS}
    load["Z1"], allocation["Z1"], targets["Z1"] = 12.81, 12.863, 81.364
    assert planning_allocation.coverage(load, allocation, []) == pytest.approx(12.81 / 12.863)
    assert planning_allocation.coverage(load, allocation, [], targets=targets) == pytest.approx(12.81 / 81.364)


def test_coverage_fixed_target_caps_credit_at_remaining_and_preserves_accents():
    load = {z: 200. for z in COMPONENTS}
    allocation = {z: 0. for z in COMPONENTS}
    targets = {z: 100. for z in COMPONENTS}
    allocation["Z2"], allocation["Z4"] = 5., 10.
    assert planning_allocation.coverage(load, allocation, ["Z4"], targets=targets) == pytest.approx(.25)


@pytest.mark.parametrize("actual,planned,remaining,expected", [
    (0., 0., 20., 1),
    (.002, .002, 20., 1),
    (.002, .003, 20., 0),
    (0., 5., 20., 0),
    (5., 0., 20., 0),
    (0., 0., 0., 0),
    (None, 0., 20., 0),
    (0., None, 20., 0),
    (0., 0., None, 0),
])
def test_quality_priority_requires_known_uncovered_direct_load(actual, planned, remaining, expected):
    objective = {"basis": "DIRECT_Q", "actual_q": actual, "planned_q": planned,
                 "remaining": remaining, "actual_effective": 30., "planned_effective": 40.}
    assert planning_allocation.quality_priority("Z4", {"Z4": objective}) == expected


def test_quality_priority_uses_direct_q_for_legacy_e_and_counts_z1_preparation():
    objectives = {
        "Z1": {"basis": "DIRECT_Q", "remaining": 50., "actual_q": 0., "planned_q": 7.9},
        "Z2": {"basis": "CANONICAL_E", "remaining": 50., "actual_q": 0., "planned_q": 0.,
               "actual": 10., "planned": 15.},
        "Z3": {"basis": "CANONICAL_E", "remaining": 50., "actual_q": None, "planned_q": 0.,
               "actual": 10., "planned": 15.},
    }
    assert planning_allocation.quality_priority("Z1", objectives) == 0
    assert planning_allocation.quality_priority("Z2", objectives) == 1
    assert planning_allocation.quality_priority("Z3", objectives) == 0


def test_quality_priority_direct_basis_fallback_does_not_treat_cascade_as_coverage():
    direct = {"basis": "DIRECT_Q", "remaining": 20., "actual": 0., "planned": 0.,
              "actual_effective": 10., "planned_effective": 15.}
    legacy = {"basis": "CANONICAL_E", "remaining": 20., "actual": 0., "planned": 0.}
    assert planning_allocation.quality_priority("Z5", {"Z5": direct}) == 1
    assert planning_allocation.quality_priority("Z5", {"Z5": legacy}) == 0


def test_nonaccent_endurance_uses_weekly_need_without_raising_coach_fraction(monkeypatch):
    repo = Repository()
    source = repo.envelope["snapshot_payload"]["load_history"]
    for a in source["activities"]:
        a["duration_min"] = 180.
    for row in source["daily"]:
        if row["effective_load"] and row["zone"] == "Z1":
            row["effective_load"] = 140.
    p = body(sessions_per_week=9, sessions_by_day=[2,1,1,2,1,2,0],
             accent_mode="MANUAL", accents=["Z4","Z5"], wave=[1.,1.,1.,.78], mixed_sessions_enabled=False)
    p["building_fraction"] = .65
    # Isolate the full endurance method; mixed candidates now legitimately
    # compete for the same slots and have their own allocation integration tests.
    p["max_key_sessions_per_week"] = 0
    original = deepcopy(p)
    plan = run(monkeypatch, p, repo)
    selected = [s for d in plan["days"] for s in planning_schedule.day_sessions(d)]
    building = [s for s in selected if s["dose_evidence"]["selection"].get("endurance_dose_from_weekly_need")]
    assert building
    assert any(s["purpose"] == "BUILDING" and s["main_work_minutes"] > s["dose_evidence"]["capacity_minutes"]*.3 for s in building)
    assert all(s["dose_evidence"]["applied_fraction"] <= p["building_fraction"] + .001 for s in selected if s["zone"] in {"Z1","Z2"})
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
    # The target cannot fit a building dose. Recovery has its own absolute
    # minimum and may use the remaining budget without filling all 21 slots.
    selected = [s for d in plan["days"] for s in d["sessions"]]
    assert selected and len(selected) < 21
    assert all(s["purpose"] == "RECOVERY" and 10 <= s["main_work_minutes"] <= 30 for s in selected)
    allocation = plan["allocation"]["components"]["Z1"]
    assert allocation["planned_effective"] <= allocation["target_effective"] + .01
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
    for session in selected:
        assert_readiness_dose(session)
        if session["purpose"] != "RECOVERY":
            evidence = session["dose_evidence"]
            assert evidence.get("applied_minimum_capacity_fraction", evidence["applied_structure_fraction"]) >= evidence["min_dose_fraction"] - .001


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
    regular = [s for s in selected if s["zone"] != "STR" and s["purpose"] != "RECOVERY" and not s.get("mixed_component")]
    assert regular
    for session in regular:
        evidence = session["dose_evidence"]
        assert_readiness_dose(session)
        assert evidence.get("applied_minimum_capacity_fraction", evidence["applied_structure_fraction"]) >= evidence["min_dose_fraction"] - .001
    # Recovery and supporting mixed blocks have their own existing minima;
    # changed expert capacities may make those methods win an allocation slot.
    for session in selected:
        if session["purpose"] == "RECOVERY":
            assert 10 <= session["main_work_minutes"] <= 30
        if session.get("mixed_component"):
            evidence = session["dose_evidence"]
            primary = sum(b["duration_min"] for b in session["blocks"] if b["kind"] == "WORK" and b["zone"] == session["zone"])
            assert primary >= evidence["minimum_primary_work_minutes"] - .001
            assert primary <= evidence["capacity_minutes"]*evidence["mixed_primary_max_fraction"] + .001
            assert evidence["applied_structure_fraction"] <= evidence["max_dose_fraction"] + .001
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


def test_old_locked_micro_session_requires_review_instead_of_bypassing_minimum(monkeypatch):
    from apps.api import training_plan_engine as engine
    from tests.api.test_management_schedule import high_capacity_history
    from tests.api.test_training_plan_engine import NOW, reference_speed
    monkeypatch.setattr(engine.model_service, "speed_view", reference_speed)
    repo = high_capacity_history()
    p = body(sessions_per_week=7)
    p["max_key_sessions_per_week"] = 0
    method = next(m for m in engine.resolved_methods(p) if m['id'] == 'RUN-REC-EASY-01')
    evidence = engine.capacity_for(method, repo.settings, None, (None, [], []), TODAY)
    session = {"zone": "Z1", "sport": "Run", "blocks": engine._blocks(method, 10., evidence, repo.settings),
               "dose_evidence": evidence, "total_minutes": 10., "is_key_session": False}
    locked = {"date": TODAY.isoformat(), "session": session, "sessions": [session]}
    original = deepcopy(locked)
    plan = engine.generate_plan(repo, "athlete", p, start_date=TODAY, now=NOW, locked_day=locked)
    assert plan["days"][0]["status"] == "REVIEW_REQUIRED"
    assert not plan["days"][0]["sessions"]
    assert any(r["code"] == "MINIMUM_CAPACITY_DOSE" for r in plan["days"][0]["rejected_alternatives"])
    assert locked == original
