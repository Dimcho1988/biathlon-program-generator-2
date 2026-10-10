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
from biathlon.component_load import calculate_component_load
from biathlon.training_methods import resolved_methods
from tests.api.test_training_plan_engine import Repository, TODAY, NOW, profile, reference_speed
from tests.api.test_readiness_adaptive_plan_v2 import assert_readiness_dose


def body(**changes):
    return profile(discipline="5000 m", age_years=30, training_experience_years=10,
                   reentry_days=0, availability_mode="AUTO_HISTORY", training_days=list(range(7)),
                   planning_controls=PlanningControls(**changes).model_dump(mode="json"))


def stored_activity_load(activity):
    effective = calculate_component_load({row["zone"]: row["equivalent_time_min"] for row in activity["zones"]})["effective"]
    for row in activity["zones"]:
        row["effective_load"] = effective[row["zone"]]
    return effective


def high_capacity_history():
    """Synthetic generous direct-Q history isolates scheduling headroom.

    Both Q and E are explicit: inflating daily E alone is discarded by the
    canonical projection and no longer describes a high-load fixture.
    """
    repo = Repository()
    source = repo.envelope["snapshot_payload"]["load_history"]
    by_day = {}
    for a in source["activities"]:
        a["duration_min"] = 180
        for row in a["zones"]:
            row["equivalent_time_min"] = 1000.
        effective = stored_activity_load(a)
        by_day[a["date"]] = effective
    for row in source["daily"]:
        row["effective_load"] = by_day.get(row["date"], {}).get(row["zone"], 0.)
    for row in source["strength"]["daily"]:
        if row["effective_load"]:
            row["effective_load"] = 1000.
    repo.envelope["activities"] = [{**deepcopy(a), "local_date": a["date"]} for a in source["activities"]]
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


def test_manual_low_z1_target_explains_blocked_training_without_overriding_the_coach(monkeypatch):
    p = body(sessions_per_week=7)
    p["max_key_sessions_per_week"] = 0
    p["component_targets_weekly"] = {"Z1": 5}
    original = deepcopy(p)
    constrained = run(monkeypatch, p)
    assert p == original
    blocked = [d for d in constrained["days"] if "Ръчна цел за Z1" in d["explanation"]]
    assert blocked
    rejection = next(r for d in blocked for r in d["rejected_alternatives"] if r.get("manual_target_components"))
    assert "Z1" in rejection["blocking_components"]
    assert "нужни" in rejection["reason"] and "остават" in rejection["reason"]
    assert all(not d["sessions"] for d in blocked)
    restored = run(monkeypatch, {**p, "component_targets_weekly": {}})
    assert restored["summary"]["sessions"] > constrained["summary"]["sessions"]
    assert restored["days"][0]["readiness_before"] == constrained["days"][0]["readiness_before"]


@pytest.mark.parametrize("count", [12, 13, 16, 21])
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


def test_second_session_can_use_building_dose_when_long_term_endurance_is_unfilled(monkeypatch):
    p = body(sessions_per_week=13, accent_mode="MANUAL", accents=["Z1"], accent_index=1.5)
    p["building_fraction"] = .65
    p["max_key_sessions_per_week"] = 0
    plan = run(monkeypatch, p)
    pairs = [d["sessions"] for d in plan["days"] if len(d["sessions"]) > 1]
    assert pairs
    second = next(sessions[1] for sessions in pairs if sessions[1]["purpose"] == "BUILDING")
    assert second["zone"] in {"Z1", "Z2"}
    # A combined method divides its building dose between its components;
    # base_fraction alone is only the primary component's share.
    assert second["dose_evidence"]["structure_base_fraction"] == p["building_fraction"]
    assert_readiness_dose(second)


def completed_activity(repo, reference, minutes=40.):
    activity = {"activity_ref": reference, "date": TODAY.isoformat(), "local_date": TODAY.isoformat(),
        "sport": "Run", "duration_min": minutes, "zones": [{"zone": zone,
        "raw_time_min": minutes if zone == "Z1" else 0.,
        "equivalent_time_min": minutes*.5 if zone == "Z1" else 0.} for zone in COMPONENTS if zone != "STR"]}
    source = repo.envelope["snapshot_payload"]["load_history"]
    effective = stored_activity_load(activity)
    source["activities"].append(deepcopy(activity))
    repo.envelope["activities"].append(deepcopy(activity))
    for row in source["daily"]:
        if row["date"] == TODAY.isoformat():
            row["effective_load"] += effective[row["zone"]]


def test_imported_first_session_leaves_a_second_slot_with_actual_load_counted_once(monkeypatch):
    repo = high_capacity_history()
    completed_activity(repo, "morning")
    original = deepcopy(repo.envelope)
    p = body(sessions_per_week=13, sessions_by_day=[2,2,2,2,2,2,1])
    p["max_key_sessions_per_week"] = 0
    plan = run(monkeypatch, p, repo)
    today = plan["days"][0]
    assert len(today["sessions"]) == 1
    assert today["activity_refs"] == ["morning"]
    assert plan["summary"]["actual_sessions"] == 1
    assert plan["summary"]["sessions"]+1 <= 13
    session = today["sessions"][0]
    assert session["total_minutes"]+40 <= 360.001
    actual_rows = engine._daily_rows(original["snapshot_payload"]["load_history"], TODAY)
    before = engine.recovery_v2.simulate(actual_rows, plan["parameters"]["recovery_settings"], target=TODAY, include_details=False)
    assert today["readiness_before"]["Z1"] == pytest.approx(before["current"][0]["readiness_percent"], abs=.002)
    assert_readiness_dose(session)
    assert repo.envelope == original
    preserved = engine.generate_plan(repo, "athlete", p, start_date=TODAY, now=NOW, locked_day=today)
    assert preserved["days"][0]["locked"]
    assert preserved["days"][0]["sessions"] == today["sessions"]
    assert preserved["days"][0]["readiness_after"] == today["readiness_after"]
    completed_activity(repo, "afternoon")
    completed = engine.generate_plan(repo, "athlete", p, start_date=TODAY, now=NOW, locked_day=today)
    assert not completed["days"][0]["sessions"]
    assert completed["summary"]["actual_sessions"] == 2


@pytest.mark.parametrize("condition", ["two_completed", "incomplete_catalog", "day_time_used"])
def test_actual_sessions_and_time_still_close_a_full_or_unknown_day(monkeypatch, condition):
    repo = high_capacity_history()
    completed_activity(repo, "morning", minutes=360. if condition == "day_time_used" else 40.)
    if condition == "two_completed":
        completed_activity(repo, "afternoon")
    elif condition == "incomplete_catalog":
        repo.envelope["activities"] = [a for a in repo.envelope["activities"] if a.get("activity_ref") != "morning"]
    p = body(sessions_per_week=13, sessions_by_day=[2,2,2,2,2,2,1])
    p["max_key_sessions_per_week"] = 0
    plan = run(monkeypatch, p, repo)
    assert not plan["days"][0]["sessions"]
    assert plan["summary"]["actual_sessions"] == (2 if condition == "two_completed" else 1)


@pytest.mark.parametrize("slots", [2,3])
def test_imported_easy_morning_keeps_the_remaining_threshold_slot_eligible(monkeypatch, slots):
    repo = high_capacity_history()
    completed_activity(repo, "morning")
    p = body(sessions_per_week=13, sessions_by_day=[slots,2,2,2,2,1,1],
        threshold_days=[TODAY.weekday()], double_threshold_days=[TODAY.weekday()],
        threshold_method="INTERVALS", accent_mode="MANUAL", accents=["Z3"])
    plan = run(monkeypatch, p, repo)
    sessions = plan["days"][0]["sessions"]
    assert len(sessions) == slots-1
    assert all(s["is_key_session"] and s["zone"] == "Z3" for s in sessions)
    assert all(s["double_threshold"] == (slots == 3) for s in sessions)
    assert sum(s["total_minutes"] for s in sessions)+40 <= 360.001
    assert plan["summary"]["actual_sessions"] == 1
    for session in sessions:
        assert_readiness_dose(session)


def test_imported_key_morning_still_enforces_key_spacing_in_the_remaining_slot(monkeypatch):
    repo = high_capacity_history()
    completed_activity(repo, "morning")
    for activities in (repo.envelope["activities"], repo.envelope["snapshot_payload"]["load_history"]["activities"]):
        activity = next(a for a in activities if a["activity_ref"] == "morning")
        zones = activity["zones"]
        next(row for row in zones if row["zone"] == "Z3").update(raw_time_min=10., equivalent_time_min=5.)
        stored_activity_load(activity)
    source = repo.envelope["snapshot_payload"]["load_history"]
    for row in source["daily"]:
        if row["date"] == TODAY.isoformat():
            row["effective_load"] = sum(zone["effective_load"] for activity in source["activities"]
                if activity["date"] == row["date"] for zone in activity["zones"] if zone["zone"] == row["zone"])
    p = body(sessions_per_week=13, sessions_by_day=[2,2,2,2,2,2,1], threshold_days=[TODAY.weekday()])
    plan = run(monkeypatch, p, repo)
    assert not any(s["is_key_session"] for s in plan["days"][0]["sessions"])


def test_double_threshold_has_independent_doses_and_long_short_structure(monkeypatch):
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
        assert not s["dose_evidence"]["shared_day_dose"]
        assert s["dose_evidence"]["minimum_dose_scope"] == "EACH_THRESHOLD_SESSION"
        assert .25 <= s["dose_evidence"]["applied_structure_fraction"] <= .5
        assert s["dose_evidence"]["planned_gap_hours"] == 6
        assert s["dose_evidence"]["readiness_policy"]["intraday_recheck"] is False
    total = sum(s["main_work_minutes"] for s in pair)
    cap = pair[0]["dose_evidence"]["capacity_minutes"]
    assert total > cap*p["building_fraction"]
    assert total <= 2*cap*.5 + .01
    assert all(6 <= b["duration_min"] <= 10 for b in pair[0]["blocks"] if b["kind"] == "WORK")
    assert all(b["duration_min"] == 1 and b["target_hr_bpm"] is None for b in pair[1]["blocks"] if b["kind"] == "WORK")
    assert not any(s["zone"] in {"Z4", "Z5"} for s in pair)
    continuous = deepcopy(p); continuous["planning_controls"]["threshold_method"] = "CONTINUOUS"
    changed = run(monkeypatch, continuous, Repository())
    assert changed["days"][0]["sessions"][0]["method_id"] == pair[0]["method_id"]


def test_double_threshold_validation_and_declining_readiness_reduces_doses(monkeypatch):
    p = body(sessions_per_week=12, double_threshold_days=[0])
    with pytest.raises(ValidationError): ManagementProfile.model_validate({**p, "age_years": None})
    with pytest.raises(ValidationError): ManagementProfile.model_validate({**p, "max_key_sessions_per_week": 1})
    with pytest.raises(ValidationError): PlanningControls(sessions_per_week=22)
    with pytest.raises(ValidationError): PlanningControls(sessions_by_day=[4,1,1,1,1,1,1])
    unready = run(monkeypatch, p)
    assert unready["days"][0]["readiness_before"]["Z3"] < 90
    pair = planning_schedule.day_sessions(unready["days"][0])
    assert len(pair) == 2 and all(s.get("double_threshold") for s in pair)
    for session in pair:
        assert_readiness_dose(session)
        assert session["dose_evidence"]["fraction"] < session["dose_evidence"]["base_fraction"]
    for z in engine.COMPONENTS:
        assert sum(s["canonical_effective_load"][z] for s in pair) <= unready["days"][0]["load_budget"]["components"][z]["deficit_effective"] + .005
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


def test_volume_report_includes_completed_microcycle_days_before_the_rolling_draft():
    anchor = TODAY-timedelta(days=3)
    source = {"activities":[
        {"date":(TODAY-timedelta(days=9)).isoformat(), "duration_min":999},
        {"date":(TODAY-timedelta(days=2)).isoformat(), "duration_min":120},
        {"date":TODAY.isoformat(), "duration_min":40}]}
    days = [{"date":(TODAY+timedelta(days=n)).isoformat(),
             "sessions":[{"total_minutes":60}] if n in {0,2,4} else []} for n in range(7)]
    volumes = planning_schedule.microcycle_volume(source, days, anchor, anchor, TODAY+timedelta(days=90))
    first, partial = volumes
    assert first["actual_minutes"] == 160
    assert first["planned_minutes"] == 120
    assert first["total_minutes"] == 280
    assert first["complete_microcycle"] is True
    assert partial["planned_minutes"] == partial["total_minutes"] == 60
    assert partial["complete_microcycle"] is False
    assert partial["through_date"] == days[-1]["date"]
    assert partial["end_date"] > partial["through_date"]


def test_legacy_unknown_planned_load_is_not_replaced_with_zero():
    day = {"session": {"title": "Older session", "sport": "Run", "total_minutes": 30,
                       "canonical_effective_load": None}}
    assert planning_schedule.day_totals(day)["canonical_effective_load"] is None
