"""Taper owns the reduction; easy exceptions remain small and accountable."""
from copy import deepcopy
from datetime import timedelta

import pytest

from apps.api import training_plan_engine as engine
from biathlon import load_progression, planning_continuity as policy, planning_controls
from biathlon.constants import COMPONENTS
from tests.api.test_load_progression import configured, observed
from tests.api.test_planning_continuity import ledger
from tests.api.test_feasible_quality_reservations import selected_methods
from tests.api.test_readiness_adaptive_plan_v2 import fixed_readiness, assert_readiness_dose
from tests.api.test_training_plan_engine import TODAY, NOW, append_complete_activity


def taper_headroom(rows, *, zone="Z1", rolling=325., manual=(), **kwargs):
    budgets = {z: {"target_weekly_effective": 300., "rolling_7d_effective": rolling,
        "rolling_deficit_effective": max(0., 300-rolling),
        "deficit_effective": min(max(0., 300-rolling), rows[z]["remaining_effective"])} for z in COMPONENTS}
    return policy.taper_limits(rows, budgets, {z: r["remaining"] for z,r in rows.items() if z != "STR"},
        zone=zone, manual=manual, **kwargs)


def test_taper_reserve_charges_actual_planned_and_rolling_excess_without_refill():
    rows = ledger(actual=106., planned=2.)
    for row in rows.values():
        row.update(actual_effective=310., planned_effective=5., remaining_effective=0.)
    before = deepcopy(rows)
    room = taper_headroom(rows, light=True)
    assert room["q"]["Z1"] == pytest.approx(2.)
    assert room["e"]["Z1"] == pytest.approx(5.)  # rolling 330 - 325, not 10% of zero remainder
    assert room["q"]["Z2"] == room["e"]["Z2"] == 0.
    assert room == taper_headroom(rows, light=True) and rows == before
    for row in rows.values():
        row.update(actual=108., actual_q=108., planned=0., planned_q=0.,
                   actual_effective=315., planned_effective=0.)
    assert taper_headroom(rows, light=True) == room  # execution replaces the proposal
    assert taper_headroom(rows, light=True, rolling=331.)["e"]["Z1"] == 0.
    direct = {z: 2. if z == "Z1" else 0. for z in COMPONENTS}
    evidence = policy.evidence(direct, direct, room,
        {"components": rows, "window_start": "2026-10-19", "window_end": "2026-10-25"})
    assert evidence["additional_q"] == {"Z1": 2.}
    assert evidence["additional_effective"] == {"Z1": 2.}
    assert evidence["rolling_limits_preserved"] is False
    assert evidence["maximum_excess_fraction"] == .1


@pytest.mark.parametrize("patch", [
    {"actual_q": None, "actual": None, "remaining": None},
    {"target": 0.}, {"target_effective": 0.}, {"basis": "CANONICAL_E"},
])
def test_missing_or_nonpositive_taper_reference_cannot_create_reserve(patch):
    rows = ledger()
    rows["Z1"].update(patch)
    assert taper_headroom(rows, light=True)["mode"] is None


def test_taper_quality_can_only_use_z1_preparation_and_manual_targets_stay_strict():
    rows = ledger(actual=98.)
    room = taper_headroom(rows, zone="Z4", preparation=True)
    assert room["q"]["Z1"] == pytest.approx(12.)
    assert room["q"]["Z4"] == 2. and room["e"]["Z4"] == 0.
    assert room["reserve_q"]["Z4"] == room["reserve_e"]["Z4"] == 0.
    rows["Z4"]["remaining"] = 0.
    assert taper_headroom(rows, zone="Z4", preparation=True)["mode"] is None
    assert taper_headroom(rows, light=True, manual={"Z1"})["mode"] is None
    assert taper_headroom(rows, zone="Z4", light=True)["mode"] is None
    easy = taper_headroom(rows, zone="Z2", light=True)
    assert set(z for z,v in easy["reserve_q"].items() if v) == {"Z1", "Z2"}


@pytest.mark.parametrize("explicit", [False, True])
def test_taper_uses_one_neutral_reference_instead_of_recovery_wave(explicit):
    body = configured()
    body["planning_controls"]["mesocycle_anchor"] = (TODAY-timedelta(days=21)).isoformat()
    if explicit:
        body["planning_controls"]["cycles"] = [{"start_date": TODAY.isoformat(),
            "end_date": (TODAY+timedelta(days=6)).isoformat(), "name": "Unloading",
            "kind": "RECOVERY", "accents": ["Z3"], "target_index": .78,
            "volume_factor": .78, "recovery_days": 0}]
    _, source, rows = observed()
    context = load_progression.context(body, source, rows, TODAY)
    phases = {"phases": [], "taper_windows": [{"start_date": TODAY.isoformat(),
        "end_date": (TODAY+timedelta(days=6)).isoformat(), "volume_factor": .7}]}
    values, _, state = engine._goals(body, TODAY, "PRECOMPETITION", True, {}, None,
        3, 4, rows, TODAY, False, .7, context, periodization=phases)
    other_wave = deepcopy(body)
    other_wave["planning_controls"]["wave"] = [.5, 1.3, 1.4, .5]
    neutral, _, _ = engine._goals(other_wave, TODAY, "PRECOMPETITION", True, {}, None,
        3, 4, rows, TODAY, False, .7, context, periodization=phases)
    assert state["kind"] == "MAINTAIN" and state["taper_priority"]["superseded_kind"] == "RECOVERY"
    assert state["wave_factor"] == state["volume_factor"] == 1.
    for z in COMPONENTS[:5]:
        assert values[z]["target"] == pytest.approx(planning_controls.reference(rows, TODAY)[z]["c40"]*7*.7)
        if not explicit:  # Explicit directives retain their existing Q/E semantics.
            assert values[z]["progression"]["cycle_shape"] == 1.
            assert values[z]["target_weekly_q"] == neutral[z]["target_weekly_q"]
        assert "recovery_reference" not in values[z]
    assert planning_controls.resolve(body, TODAY, "PRECOMPETITION", ["Z3"], periodization=phases)["kind"] == "MAINTAIN"
    assert planning_controls.resolve(body, TODAY, "PRECOMPETITION", ["Z3"], taper=False)["kind"] == "RECOVERY"
    assert planning_controls.resolve(body, TODAY, "RE_ENTRY", ["Z3"], taper=True)["kind"] == "RE_ENTRY"


def easy_taper(monkeypatch):
    fixed_readiness(monkeypatch, 100.)
    body = configured(age_years=25, training_experience_years=5, available_minutes=[180]*7)
    body["planning_controls"].update(sessions_per_week=7, sessions_by_day=[2,0,1,1,1,1,1],
        mixed_sessions_enabled=False, accents=["Z1"])
    selected_methods(monkeypatch, body, {"RUN-REC-EASY-01"})
    monkeypatch.setattr(engine, "_phase", lambda *args: ("PRECOMPETITION", True))
    monkeypatch.setattr(engine, "_taper_factor", lambda *args: .7)
    original = engine._goals
    def goals(*args, **kwargs):
        values, accents, state = original(*args, **kwargs)
        for z in COMPONENTS:
            values[z].update(target=1000., target_weekly_q=1000.)
        return values, accents, state
    monkeypatch.setattr(engine, "_goals", goals)
    repo, source, _ = observed()
    return body, repo, source


def test_taper_daily_cap_is_shared_by_all_sessions_and_preserved_approval(monkeypatch):
    body, repo, _ = easy_taper(monkeypatch)
    plan = engine.generate_plan(repo, "athlete", body, start_date=TODAY, now=NOW)
    cap = plan["parameters"]["baseline_weekly_minutes"]/7*.7
    assert plan["days"][0]["sessions"]
    for day in plan["days"]:
        assert sum(s["total_minutes"] for s in day["sessions"]) <= cap+.002
        for session in day["sessions"]:
            assert_readiness_dose(session)
    locked = deepcopy(plan["days"][0])
    locked["sessions"] = [deepcopy(locked["sessions"][0]) for _ in range(2)]
    assert sum(s["total_minutes"] for s in locked["sessions"]) > cap+.002
    refreshed = engine.generate_plan(repo, "athlete", body, start_date=TODAY, now=NOW, locked_day=locked)
    assert refreshed["days"][0]["status"] == "REVIEW_REQUIRED"


def test_executed_minutes_also_consume_the_same_daily_taper_cap(monkeypatch):
    body, repo, source = easy_taper(monkeypatch)
    activity = {"activity_ref": "already-executed", "date": TODAY.isoformat(),
        "sport": "Run", "duration_min": 28., "start_at_utc": TODAY.isoformat()+"T06:00:00Z",
        "zones": [{"zone": z, "raw_time_min": 28. if z == "Z1" else 0.,
            "equivalent_time_min": 20. if z == "Z1" else 0.} for z in COMPONENTS if z != "STR"]}
    append_complete_activity(source, activity)
    repo.envelope["activities"].append({**deepcopy(activity), "local_date": activity["date"]})
    plan = engine.generate_plan(repo, "athlete", body, start_date=TODAY, now=NOW)
    cap = plan["parameters"]["baseline_weekly_minutes"]/7*.7
    assert cap > 28.
    assert 28.+sum(s["total_minutes"] for s in plan["days"][0]["sessions"]) <= cap+.002


def test_engine_can_use_whole_easy_dose_after_target_but_cannot_refill_reserve(monkeypatch):
    body, repo, source = easy_taper(monkeypatch)
    body["planning_controls"]["mesocycle_anchor"] = (TODAY-timedelta(days=3)).isoformat()
    original = engine._goals
    def goals(*args, **kwargs):
        values, accents, state = original(*args, **kwargs)
        values["Z1"]["target_weekly_q"] = 100.
        return values, accents, state
    monkeypatch.setattr(engine, "_goals", goals)
    activity = {"activity_ref": "covered-easy-target", "date": (TODAY-timedelta(days=2)).isoformat(),
        "sport": "Run", "duration_min": 125., "zones": [
            {"zone": z, "raw_time_min": 125. if z == "Z1" else 0.,
             "equivalent_time_min": 100. if z == "Z1" else 0.} for z in COMPONENTS if z != "STR"]}
    append_complete_activity(source, activity)
    repo.envelope["activities"].append({**deepcopy(activity), "local_date": activity["date"]})
    plan = engine.generate_plan(repo, "athlete", body, start_date=TODAY, now=NOW)
    same_cycle = [s for d in plan["days"] if d["date"] <= (TODAY+timedelta(days=3)).isoformat()
                  for s in d["sessions"]]
    assert same_cycle
    assert sum(s["direct_equivalent_minutes"]["Z1"] for s in same_cycle) <= 10.005
    for session in same_cycle:
        assert session["dose_evidence"]["continuity"]["mode"] == "TAPER_LIGHT_MAINTENANCE"
        assert_readiness_dose(session)
    again = engine.generate_plan(repo, "athlete", body, start_date=TODAY, now=NOW)
    assert again["days"] == plan["days"]
    locked = deepcopy(plan["days"][0])
    assert locked["sessions"]
    approved = engine.generate_plan(repo, "athlete", body, start_date=TODAY, now=NOW, locked_day=locked)
    assert approved["days"][0]["locked"]
    assert approved["days"][0]["sessions"][0]["blocks"] == locked["sessions"][0]["blocks"]
