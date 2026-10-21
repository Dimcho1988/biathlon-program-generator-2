"""Executed targets can permit bounded maintenance, never free extra load."""
from copy import deepcopy
from datetime import timedelta

import pytest

from biathlon import planning_continuity as policy
from biathlon.constants import COMPONENTS
from apps.api import training_plan_engine as engine
from tests.api.test_load_progression import configured, observed
from tests.api.test_feasible_quality_reservations import selected_methods
from tests.api.test_readiness_adaptive_plan_v2 import fixed_readiness, assert_readiness_dose
from tests.api.test_training_plan_engine import TODAY, NOW, append_complete_activity


def ledger(actual=100., planned=0.):
    return {z: dict(basis="DIRECT_Q", target=100., actual=actual, actual_q=actual,
        planned=planned, planned_q=planned, remaining=max(0., 100-actual-planned),
        target_effective=300., actual_effective=actual, planned_effective=planned,
        remaining_effective=max(0., 300-actual-planned)) for z in COMPONENTS}


def reference(zone, q=20.):
    direct = {z:q if z == zone else 5. if z == "Z1" else 0. for z in COMPONENTS}
    return {"q":direct, "e":direct, "method_id":"reference"}


def headroom(rows, *, primary="Z3", rolling=150., references=None, **kwargs):
    budgets = {z:{"deficit_effective":min(rolling, rows[z]["remaining_effective"]),
                  "rolling_deficit_effective":rolling} for z in COMPONENTS}
    q = {z:rows[z]["remaining"] for z in COMPONENTS if z != "STR"}
    reserve = policy.reserves(rows, references or {primary:reference(primary)})
    return policy.limits(rows, budgets, q, reserve, zone=primary, **kwargs)


def test_excess_is_charged_once_against_the_original_target_across_replans():
    rows = ledger(actual=108., planned=4.)
    before = deepcopy(rows)
    first = headroom(rows, maintenance=True)
    assert first["q"]["Z3"] == 8.  # 100 + one 20 Q dose - 108 - 4
    assert first == headroom(rows, maintenance=True)
    assert rows == before
    for z in COMPONENTS:
        rows[z].update(actual=112., actual_q=112., planned=0., planned_q=0.)
    assert headroom(rows, maintenance=True)["q"]["Z3"] == 8.  # imported execution replaces proposal


def test_planned_exhaustion_and_unknown_execution_cannot_create_maintenance_reserves():
    rows = ledger(actual=0., planned=100.)
    assert not policy.reserves(rows, {"Z3":reference("Z3")})["references"]
    rows["Z3"].update(actual=None, actual_q=None)
    assert not policy.reserves(rows, {"Z3":reference("Z3")})["references"]


def test_overshoot_and_manual_components_cannot_borrow_another_components_allowance():
    rows = ledger(actual=130.)
    assert headroom(rows, maintenance=True)["q"]["Z3"] == 0.
    reserve = policy.reserves(ledger(), {"Z3":reference("Z3")}, {"Z3"})
    assert not reserve["references"]
    allowed = headroom(ledger(), maintenance=True)
    assert allowed["q"]["Z4"] == 0.
    q = {z:1. if z == "Z4" else 0. for z in COMPONENTS}
    assert not policy.fits(q, q, allowed, ledger())


def test_preparation_is_ten_percent_once_for_an_uncovered_quality_only():
    rows = ledger(actual=0.)
    rows["Z1"].update(actual=102., actual_q=102., remaining=0.)
    allowed = headroom(rows, preparation=True)
    assert allowed["q"]["Z1"] == 8.
    rows["Z1"].update(planned=6., planned_q=6.)
    assert headroom(rows, preparation=True)["q"]["Z1"] == 2.
    rows["Z3"]["remaining"] = 0.
    assert headroom(rows, preparation=True)["mode"] is None


def test_rolling_effective_limit_is_not_relaxed_by_either_reserve():
    rows = ledger()
    allowed = headroom(rows, maintenance=True, rolling=0.)
    q = {z:1. if z == "Z3" else 0. for z in COMPONENTS}
    assert not policy.fits(q, q, allowed, rows)
    assert allowed["e"]["Z3"] == 0.


def scenario(monkeypatch, *, readiness=100., actual_q=30., manual=False, rest=False, rolling=500., unloading=None):
    fixed_readiness(monkeypatch, readiness)
    body = configured(age_years=25, training_experience_years=5, available_minutes=[180]*7)
    body["planning_controls"].update(mesocycle_anchor=(TODAY-timedelta(days=3)).isoformat(),
        sessions_per_week=7, sessions_by_day=[0 if rest else 1]*7,
        threshold_days=[1,4], threshold_method="INTERVALS", mixed_sessions_enabled=False)
    if manual:
        body["component_targets_weekly"] = {"Z3":500.}
    selected_methods(monkeypatch, body, {"END-THR-TIME-01"})
    original = engine._goals
    def goals(*args, **kwargs):
        values, accents, cycle = original(*args, **kwargs)
        for z in COMPONENTS:
            values[z].update(target=rolling, target_weekly_q=30. if z == "Z3" else 300.)
        return values, accents, cycle
    monkeypatch.setattr(engine, "_goals", goals)
    if unloading == "TAPER":
        monkeypatch.setattr(engine, "_phase", lambda *args:("PRECOMPETITION", True))
    elif unloading == "RE_ENTRY":
        monkeypatch.setattr(engine, "reentry_dose_active", lambda *args:True)
    repo, source, _ = observed()
    activity = {"activity_ref":"executed-target", "date":(TODAY-timedelta(days=3)).isoformat(),
        "sport":"Run", "duration_min":actual_q+20., "zones":[
            {"zone":z, "raw_time_min":actual_q if z == "Z3" else 20. if z == "Z1" else 0.,
             "equivalent_time_min":actual_q if z == "Z3" else 20. if z == "Z1" else 0.}
            for z in COMPONENTS if z != "STR"]}
    append_complete_activity(source, activity)
    repo.envelope["activities"].append({**deepcopy(activity), "local_date":activity["date"]})
    return body, repo


def adaptive_sessions(plan):
    return [s for d in plan["days"] for s in d["sessions"]
            if s["dose_evidence"].get("continuity", {}).get("mode") == "POST_TARGET_MAINTENANCE"]


def test_engine_prescribes_reduced_whole_maintenance_after_actual_target_and_does_not_refill(monkeypatch):
    body, repo = scenario(monkeypatch)
    original = deepcopy(repo.envelope)
    plan = engine.generate_plan(repo, "athlete", body, start_date=TODAY, now=NOW)
    sessions = adaptive_sessions(plan)
    assert sessions
    first = sessions[0]
    assert first["purpose"] == "MAINTENANCE" and first["is_key_session"]
    assert_readiness_dose(first)
    reference = plan["allocation"]["continuity"]["reference_doses"][(TODAY-timedelta(days=3)).isoformat()]["Z3"]
    assert sum(s["direct_equivalent_minutes"]["Z3"] for s in sessions) <= reference["q"]["Z3"]+.005
    assert repo.envelope == original
    again = engine.generate_plan(repo, "athlete", body, start_date=TODAY, now=NOW)
    assert plan["days"] == again["days"]
    assert plan["fingerprint"] == again["fingerprint"]


@pytest.mark.parametrize("changes", [{"readiness":40.}, {"actual_q":100.}, {"manual":True},
                                     {"rest":True}, {"rolling":10.}, {"unloading":"TAPER"},
                                     {"unloading":"RE_ENTRY"}])
def test_engine_keeps_recovery_overshoot_manual_rest_and_rolling_gates(monkeypatch, changes):
    body, repo = scenario(monkeypatch, **changes)
    plan = engine.generate_plan(repo, "athlete", body, start_date=TODAY, now=NOW)
    assert not adaptive_sessions(plan)


def test_taper_rest_explains_bounded_easy_reserve_cannot_extend_quality_work(monkeypatch):
    body, repo = scenario(monkeypatch, actual_q=25., unloading="TAPER")
    plan = engine.generate_plan(repo, "athlete", body, start_date=TODAY, now=NOW)
    blocked = [d for d in plan["days"] if d["status"] == "REST" and any(
        r["code"] == "DIRECT_Q_PROGRESSION_BUDGET" for r in d["rejected_alternatives"])]
    assert blocked
    for day in blocked:
        assert "Ограниченият тейпър резерв до 10%" in day["explanation"]
        assert "не увеличава бюджета за основната работа в Z3–Z5" in day["explanation"]
    assert not adaptive_sessions(plan)


def test_approved_maintenance_is_revalidated_without_replenishing_the_reserve(monkeypatch):
    body, repo = scenario(monkeypatch)
    plan = engine.generate_plan(repo, "athlete", body, start_date=TODAY, now=NOW)
    assert adaptive_sessions(plan)
    locked = deepcopy(plan["days"][0])
    assert locked["sessions"]
    refreshed = engine.generate_plan(repo, "athlete", body, start_date=TODAY, now=NOW, locked_day=locked)
    assert refreshed["days"][0]["locked"]
    assert refreshed["days"][0]["sessions"][0]["blocks"] == locked["sessions"][0]["blocks"]
    assert sum(s["direct_equivalent_minutes"]["Z3"] for s in adaptive_sessions(refreshed)) <= (
        sum(s["direct_equivalent_minutes"]["Z3"] for s in adaptive_sessions(plan)) + .005)


def test_empty_replay_day_does_not_erase_future_strength_preparation(monkeypatch):
    fixed_readiness(monkeypatch, 100.)
    body = configured(age_years=25, training_experience_years=5, strength_enabled=True,
                      available_minutes=[180]*7)
    body["planning_controls"].update(sessions_per_week=2, sessions_by_day=[1,0,0,0,1,0,0],
        accents=["Z1"], strength_days=[4], mixed_sessions_enabled=False)
    selected_methods(monkeypatch, body, {"END-LONG-Z1-01-BUILD", "STR-CIRCUIT-RUN-01"})
    body["_allocation_policy"] = {"reservation_methods":{
        TODAY.isoformat():[{"method_id":"END-LONG-Z1-01-BUILD", "sport":"Run", "slot":1}],
        (TODAY+timedelta(days=4)).isoformat():[]}}
    original = engine._goals
    def goals(*args, **kwargs):
        values, accents, cycle = original(*args, **kwargs)
        values["Z1"].update(target=500., target_weekly_q=100.)
        values["STR"]["target"] = 60.
        return values, accents, cycle
    monkeypatch.setattr(engine, "_goals", goals)
    repo, _, _ = observed()
    plan = engine.generate_plan(repo, "athlete", body, start_date=TODAY, now=NOW)
    assert plan["days"][0]["sessions"][0]["zone"] == "Z1"
    assert any(s["zone"] == "STR" for s in plan["days"][4]["sessions"])
    assert sum(s["direct_equivalent_minutes"]["Z1"] for d in plan["days"] for s in d["sessions"]) <= 100.005


def test_recovery_trial_spreads_complete_doses_across_training_days(monkeypatch):
    fixed_readiness(monkeypatch, 100.)
    body = configured(age_years=25, training_experience_years=5, available_minutes=[180]*7)
    body["planning_controls"].update(sessions_per_week=4, sessions_by_day=[2,0,1,0,1,0,0],
        accents=["Z1"], mixed_sessions_enabled=False)
    body["_allocation_policy"] = {"spread_recovery":True, "balance_complete_doses":False}
    selected_methods(monkeypatch, body, {"RUN-REC-EASY-01"})
    original = engine._goals
    def goals(*args, **kwargs):
        values, accents, cycle = original(*args, **kwargs)
        values["Z1"].update(target=500., target_weekly_q=30.)
        return values, accents, cycle
    monkeypatch.setattr(engine, "_goals", goals)
    repo, _, _ = observed()
    plan = engine.generate_plan(repo, "athlete", body, start_date=TODAY, now=NOW)
    assert [len(d["sessions"]) for d in plan["days"]] == [1,0,1,0,1,0,0]
    for d in plan["days"]:
        for s in d["sessions"]:
            assert_readiness_dose(s)
    assert sum(s["direct_equivalent_minutes"]["Z1"] for d in plan["days"] for s in d["sessions"]) <= 30.005


def test_search_prefers_distribution_without_extra_preparation_after_covering_priorities():
    from apps.api.planning_search import score, improve
    day = {"date":"2026-10-20", "sessions":[{"zone":"Z1", "method_id":"easy", "sport":"Run",
           "main_work_minutes":60., "purpose":"RECOVERY", "slot":1}]}
    plan = {"days":[day, {"date":"2026-10-22", "sessions":[]}],
        "parameters":{"allocation_policy":{"balance_complete_doses":False}},
        "allocation":{"components":{
            "Z1":{"target":100., "actual":0., "planned":100., "remaining":0.},
            "Z3":{"target":30., "actual":0., "planned":20., "remaining":10.}}}}
    excess = deepcopy(plan)
    excess["allocation"]["continuity"] = {"sessions":[
        {"mode":"QUALITY_PREPARATION_RESERVE", "additional_q":{"Z1":5.}}]}
    assert score(plan, {"Z3"}) > score(excess, {"Z3"})
    calls = []
    def replay(policy):
        calls.append(policy)
        return deepcopy(plan)
    improve(plan, {"planning_controls":{"accents":["Z3"]}}, replay)
    assert calls[-1]["spread_recovery"]
    assert calls[-1]["balance_complete_doses"] is False
    assert calls[-1]["easy_cap_zones"] == ["Z1"]
    assert calls[-1]["easy_caps"] == {"2026-10-20":45.}
    calls.clear()
    improve(plan, {"planning_controls":{"accents":["Z3"],
        "sessions_by_day":[1,1,1,0,1,1,1]}}, replay)
    assert not any(policy.get("spread_recovery") for policy in calls)
