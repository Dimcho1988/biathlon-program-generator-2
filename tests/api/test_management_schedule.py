"""Regression tests for calendar edits, multi-session days and shared budgets."""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from apps.api import training_plan_engine as engine, management_service, management_lifecycle
from apps.api.management_schemas import ManagementProfile, PlanningControls
from biathlon import planning_schedule
from biathlon.constants import COMPONENTS
from biathlon.training_methods import resolved_methods
from tests.api.test_training_plan_engine import Repository, TODAY, NOW, profile, reference_speed


def body(**changes):
    return profile(discipline="5000 m", age_years=30, training_experience_years=10,
                   reentry_days=0, availability_mode="AUTO_HISTORY", training_days=list(range(7)),
                   planning_controls=PlanningControls(**changes).model_dump(mode="json"))


def high_capacity_history():
    """Synthetic generous history isolates scheduling from exposure scarcity."""
    repo = Repository()
    source = repo.envelope["snapshot_payload"]["load_history"]
    for r in source["daily"] + source["strength"]["daily"]:
        if r["effective_load"]:
            r["effective_load"] = 1000.
    for a in source["activities"]:
        a["duration_min"] = 180
    return repo


def run(monkeypatch, p, repo=None):
    monkeypatch.setattr(engine.model_service, "speed_view", reference_speed)
    return engine.generate_plan(repo or high_capacity_history(), "athlete", p, start_date=TODAY, now=NOW)


def test_calendar_edit_moves_both_horizons_without_rewriting_profile(monkeypatch):
    repo = Repository()
    repo.active_analysis = lambda _: deepcopy(repo.envelope)
    saved = {"configured": True, "revision": 1, "profile": body()}
    original = deepcopy(saved)
    monkeypatch.setattr(management_service, "ManagementStore", lambda _: SimpleNamespace(profile=lambda _: deepcopy(saved)))
    first = run(monkeypatch, saved["profile"], repo)
    repo.events[0].update(start_date="2027-02-10", end_date="2027-02-12")
    second = run(monkeypatch, saved["profile"], repo)
    outlook = management_service.outlook(repo, "athlete", now=NOW)["outlook"]
    assert first["fingerprint"] != second["fingerprint"]
    assert second["parameters"]["horizon"]["effective_end"] == "2027-02-12"
    assert second["periodization"] == outlook["periodization"]
    assert second["long_term"]["weeks"][-1]["end_date"] == "2027-02-12"
    assert saved == original
    manual = run(monkeypatch, {**saved["profile"], "horizon_mode": "MANUAL"}, repo)
    assert manual["parameters"]["horizon"]["effective_end"] == saved["profile"]["program_end"]
    assert any(w["code"] == "MAIN_RACE_OUTSIDE_HORIZON" for w in manual["warnings"])


def test_near_race_edit_rearranges_week_and_taper(monkeypatch):
    repo = Repository()
    first = run(monkeypatch, body(), repo)
    repo.events[0].update(start_date=(TODAY+timedelta(days=3)).isoformat(), end_date=(TODAY+timedelta(days=3)).isoformat())
    second = run(monkeypatch, body(), repo)
    assert second["days"][-1]["status"] == "RACE"
    assert any(d["taper"] for d in second["days"][:-1])
    assert first["days"] != second["days"]


@pytest.mark.parametrize("count", [12, 16, 21])
def test_more_than_seven_sessions_are_real_and_all_loads_are_counted(monkeypatch, count):
    p = body(sessions_per_week=count)
    p["max_key_sessions_per_week"] = 0
    plan = run(monkeypatch, p)
    sessions = [s for d in plan["days"] for s in planning_schedule.day_sessions(d)]
    assert len(plan["days"]) == len({d["date"] for d in plan["days"]}) == 7
    assert len(sessions) == count
    assert plan["summary"]["sessions"] == len(sessions)
    assert plan["summary"]["planned_minutes"] == pytest.approx(sum(s["total_minutes"] for s in sessions), abs=.002)
    for d in plan["days"]:
        ss = planning_schedule.day_sessions(d)
        assert len(ss) <= 3 and sum(s["total_minutes"] for s in ss) <= 360.001
        for z in COMPONENTS:
            assert sum(s["canonical_effective_load"][z] for s in ss) <= d["load_budget"]["components"][z]["deficit_effective"] + .005
        if ss:
            assert d["readiness_scope"] == "DAY_START_BUNDLE_NOT_INTRADAY_FORECAST"
            assert d["readiness_after"]["Z1"] < d["readiness_before"]["Z1"]


def test_day_and_session_preferences_change_the_generated_week(monkeypatch):
    p = body(sessions_per_week=21, sessions_by_day=[3,0,2,0,3,0,2])
    p["max_key_sessions_per_week"] = 0
    first = run(monkeypatch, p)
    for d in first["days"]:
        assert len(planning_schedule.day_sessions(d)) <= p["planning_controls"]["sessions_by_day"][engine.date.fromisoformat(d["date"]).weekday()]
    limited = {**deepcopy(p), "availability_mode": "MANUAL", "available_minutes": [30]*7}
    second = run(monkeypatch, limited)
    assert first["summary"]["planned_minutes"] > second["summary"]["planned_minutes"]
    assert all(sum(s["total_minutes"] for s in planning_schedule.day_sessions(d)) <= 30.001 for d in second["days"])


def test_double_threshold_is_two_sessions_sharing_one_work_dose(monkeypatch):
    p = body(sessions_per_week=12, double_threshold_days=[TODAY.weekday()], threshold_method="INTERVALS",
             accent_mode="MANUAL", accents=["Z3"], accent_index=1.5)
    plan = run(monkeypatch, p, Repository())
    pair = planning_schedule.day_sessions(plan["days"][0])
    assert len(pair) == 2 and all(s["double_threshold"] for s in pair)
    assert plan["summary"]["key_sessions"] == 2
    for s in pair:
        assert s["zone"] == "Z3"
        assert any(b["kind"] == "WARMUP" for b in s["blocks"])
        assert any(b["kind"] == "COOLDOWN" for b in s["blocks"])
        assert s["dose_evidence"]["shared_day_dose"]
    total = sum(s["main_work_minutes"] for s in pair)
    cap = pair[0]["dose_evidence"]["capacity_minutes"]
    assert total <= cap*p["building_fraction"] + .01
    assert not any(s["zone"] in {"Z4", "Z5"} for s in pair)
    continuous = deepcopy(p); continuous["planning_controls"]["threshold_method"] = "CONTINUOUS"
    changed = run(monkeypatch, continuous, Repository())
    assert changed["days"][0]["sessions"][0]["method_id"] != pair[0]["method_id"]


def test_double_threshold_validation_and_declining_readiness(monkeypatch):
    p = body(sessions_per_week=12, double_threshold_days=[0])
    with pytest.raises(ValidationError): ManagementProfile.model_validate({**p, "age_years": None})
    with pytest.raises(ValidationError): ManagementProfile.model_validate({**p, "max_key_sessions_per_week": 1})
    with pytest.raises(ValidationError): PlanningControls(sessions_per_week=22)
    with pytest.raises(ValidationError): PlanningControls(sessions_by_day=[4,1,1,1,1,1,1])
    unready = run(monkeypatch, p)
    assert unready["days"][0]["readiness_before"]["Z3"] < 90
    assert not any(s.get("double_threshold") for s in planning_schedule.day_sessions(unready["days"][0]))
    p["reentry_days"] = 7; p["program_start"] = TODAY.isoformat()
    assert not any(s.get("double_threshold") for d in run(monkeypatch, p)["days"] for s in planning_schedule.day_sessions(d))


def test_nonlinear_spill_is_per_session_and_daily_forecast_is_additive():
    settings = Repository().settings
    block = engine._block("WORK", "Threshold", "Z3", 60, 158, "Controlled")
    rows = engine._daily_rows(Repository().envelope["snapshot_payload"]["load_history"], TODAY)
    single = engine._canonical_load([block], settings, rows, TODAY)[1]
    pair = engine._candidate_load([{**block, "session_index": i} for i in (1,2)], settings, rows, TODAY)[1]
    assert pair == pytest.approx({z: 2*v for z,v in single.items()})
    first = engine._add_forecast_session(rows, TODAY, single)
    both = engine._add_forecast_session(first, TODAY, single)
    assert {r["zone"]: r["effective_load"] for r in both if r["date"] == TODAY.isoformat()} == pytest.approx(pair)
    assert len({(r["date"], r["zone"]) for r in both}) == len(both)


def test_second_session_changes_are_visible_in_reconciliation_and_adaptation():
    first = {"title": "Morning", "sport": "Run", "total_minutes": 40, "method_id": "easy", "blocks": [], "canonical_effective_load": {z:10 for z in COMPONENTS}}
    second = {**deepcopy(first), "title": "Afternoon", "total_minutes": 30}
    d = {"date": (TODAY-timedelta(days=1)).isoformat(), "status": "TRAINING", "session": first, "sessions": [first,second], "explanation": "Two sessions"}
    plan = {"days": [d]}
    outcomes = management_lifecycle.reconcile(plan, {"activities": []}, TODAY, {})
    assert outcomes[0]["planned_minutes"] == 70
    assert outcomes[0]["planned_load"] == {z:20 for z in COMPONENTS}
    changed = deepcopy(plan); changed["days"][0]["sessions"][1]["total_minutes"] = 20
    changes = management_lifecycle.changes_between(plan, changed)
    assert changes[0]["before_minutes"] == 70 and changes[0]["after_minutes"] == 60


def test_metabolic_total_can_exceed_continuous_capacity_only_through_profile():
    method = next(m for m in resolved_methods(body()) if m["zone"] == "Z4")
    cap = engine.capacity_for(method, Repository().settings, None, (None,[],[]), TODAY)
    cap["capacity_minutes"] = 10
    cap["effort_profile"]["continuous_capacity_min"] = 10
    work = min(method["max_work_min"], 10*method["interval_template"]["total_capacity_ratio"])
    blocks = engine._blocks(method, work, cap, Repository().settings)
    assert sum(b["duration_min"] for b in blocks if b["kind"] == "WORK") == 12
    assert [b["duration_min"] for b in blocks if b["kind"] == "RECOVERY"] == [3,3,3]
    assert all(b["duration_min"] < 10 for b in blocks if b["kind"] == "WORK")


def test_scarce_slots_reserve_the_selected_weekdays_before_optional_easy_days():
    p = body(sessions_per_week=2, intensity_days=[4], strength_days=[1])
    schedule = planning_schedule.slots(p, [360]*7, TODAY, TODAY+timedelta(days=6))
    assert {d.weekday() for d, _, count in schedule if count} == {1,4}


def test_legacy_unknown_planned_load_is_not_replaced_with_zero():
    day = {"session": {"title": "Older session", "sport": "Run", "total_minutes": 30,
                       "canonical_effective_load": None}}
    assert planning_schedule.day_totals(day)["canonical_effective_load"] is None
