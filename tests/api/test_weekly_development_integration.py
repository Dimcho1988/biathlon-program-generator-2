"""Real engine recipes meet frequency without bypassing any daily budget."""
from copy import deepcopy
from datetime import timedelta

import pytest

from apps.api import training_plan_engine as engine
from biathlon import daily_intensity, planning_development
from biathlon.constants import COMPONENTS
from tests.api.test_management_schedule import body, run, high_capacity_history
from tests.api.test_mesocycle_focus import configured
from tests.api.test_readiness_adaptive_plan_v2 import fixed_readiness
from tests.api.test_training_plan_engine import TODAY, NOW, Repository


def aerobic_profile(target, desired):
    profile = body(sessions_per_week=7, sessions_by_day=[1]*7,
        mesocycle_anchor=TODAY.isoformat(), accent_mode="MANUAL", accents=["Z1"],
        wave=[1., 1., 1., .78])
    profile["max_key_sessions_per_week"] = desired
    profile["component_targets_weekly"] = {z: target if z == "Z1" else 0. for z in COMPONENTS}
    return profile


def assert_daily_limits(plan):
    for day in [*plan["days"], *plan.get("lookahead_days", [])]:
        budget = day["daily_intensity_budget"]
        assert budget["used_fraction"] <= budget["limit_fraction"]+daily_intensity.EPS
        for z in COMPONENTS:
            prescribed = sum(s["canonical_effective_load"][z] for s in day["sessions"])
            assert prescribed <= day["load_budget"]["components"][z]["deficit_effective"]+.005


@pytest.mark.parametrize("target,desired,minimum", [(300., 2, 2), (600., 3, 3)])
def test_feasible_loading_frequency_survives_candidate_dose_sharing(monkeypatch, target, desired, minimum):
    # Generous explicitly recorded Q isolates distribution; the genuine
    # component/time/daily-dose checks remain active. Only readiness is fixed.
    fixed_readiness(monkeypatch, 100.)
    profile = aerobic_profile(target, desired)
    plan = run(monkeypatch, profile)
    frequency = plan["allocation"]["development"]["segments"][0]
    assert frequency["planned"] >= minimum
    assert frequency["minimum_shortfall"] == 0
    assert frequency["target_shortfall"] == 0
    assert plan["summary"]["key_sessions"] == 0  # Aerobic building is not a high-zone key.
    assert 90 <= plan["allocation"]["segments"][0]["components"]["Z1"]["coverage_percent"] <= 110
    assert all(s["execution_descriptor"]["developmental"] == planning_development.session_evidence(s)
               for day in plan["days"] for s in day["sessions"])
    assert_daily_limits(plan)


@pytest.mark.parametrize("available,expected_supplements", [(60, 0), (70, 2)])
def test_optional_preparation_preserves_two_real_developmental_doses(
        monkeypatch, available, expected_supplements):
    from tests.api.test_load_progression import observed
    from tests.api.test_long_term_scheduling import assert_segment_contract
    from tests.api.test_quality_reservation_feasibility import constrained_profile, selected_methods

    fixed_readiness(monkeypatch, 100.)
    original = engine._goals
    def goals(*args, **kwargs):
        values, accents, cycle = original(*args, **kwargs)
        for zone, target in {"Z1": 500., "Z2": 80., "Z3": 70., "Z4": 20.}.items():
            values[zone].update(target_weekly_q=target, desired_weekly_q=target)
        return values, accents, cycle
    monkeypatch.setattr(engine, "_goals", goals)
    profile = constrained_profile([0, available, 0, 0, available, 0, 0], [0, 2, 0, 0, 2, 0, 0])
    profile["planning_controls"].update(mesocycle_anchor=TODAY.isoformat(), threshold_days=[1, 4])
    profile["max_key_sessions_per_week"] = 2
    selected_methods(monkeypatch, profile,
        {"END-THR-TIME-01-FLEX", "END-CROSS-TRAIN-01-Z2-MIX-STEADY"})
    repository, _, _ = observed()

    plan = engine.generate_plan(repository, "athlete", profile, start_date=TODAY, now=NOW)

    sessions = [session for day in plan["days"] for session in day["sessions"]]
    building = [session for session in sessions if session["zone"] == "Z3"]
    assert len(building) == 2
    assert all(planning_development.is_developmental(session) for session in building)
    frequency = plan["allocation"]["development"]["segments"][0]
    assert frequency["planned"] == 2 and frequency["minimum_shortfall"] == 0
    assert sum(session["zone"] == "Z2" for session in sessions) == expected_supplements
    for session in building:
        work = [block for block in session["blocks"] if block["kind"] == "WORK"]
        assert len(work) >= 2 and all(6. <= block["duration_min"] <= 12. for block in work)
    if available == 60:
        # A 2-minute supplement previously shrank each legal 34.5-minute
        # building task to 13 minutes, erasing both developmental occasions.
        assert all(session["main_work_minutes"] == 34.5 for session in building)
        assert 90. <= plan["allocation"]["segments"][0]["components"]["Z3"]["coverage_percent"] <= 110.
    for day in plan["days"]:
        assert sum(session["total_minutes"] for session in day["sessions"]) <= available+.005
    assert_segment_contract(plan)
    assert_daily_limits(plan)


def test_recovery_keeps_complementary_support_and_only_maintenance(monkeypatch):
    fixed_readiness(monkeypatch, 100.)
    profile = configured()
    profile["planning_controls"]["mesocycle_anchor"] = (TODAY-timedelta(days=21)).isoformat()
    plan = run(monkeypatch, profile, Repository())
    assert plan["summary"]["sessions"] > 0
    for day in plan["days"]:
        assert day["cycle"]["kind"] == "RECOVERY"
        assert day["cycle"]["accents"]
        assert not set(day["cycle"]["accents"]) & set(day["cycle"]["mesocycle_accents"])
        assert all(s["purpose"] != "BUILDING" for s in day["sessions"])
    assert plan["allocation"]["development"]["segments"][0]["target"] == 0
    assert not plan["allocation"]["development"]["needs_search"]
    assert_daily_limits(plan)


def test_explicit_maintenance_cycle_cannot_take_a_building_recipe(monkeypatch):
    fixed_readiness(monkeypatch, 100.)
    profile = aerobic_profile(600., 3)
    profile["planning_controls"]["cycles"] = [{"kind": "MAINTAIN", "name": "Поддържане",
        "start_date": TODAY.isoformat(), "end_date": (TODAY+timedelta(days=6)).isoformat(),
        "accents": ["Z1"], "target_index": 1., "volume_factor": 1., "recovery_days": 7}]
    plan = run(monkeypatch, profile)
    assert plan["summary"]["sessions"] > 0
    assert all(s["purpose"] != "BUILDING" for day in plan["days"] for s in day["sessions"])
    assert plan["allocation"]["development"]["segments"][0]["target"] == 0
    assert_daily_limits(plan)


def test_explicit_double_threshold_counts_its_two_separate_executable_sessions(monkeypatch):
    fixed_readiness(monkeypatch, 100.)
    profile = body(sessions_per_week=12, double_threshold_days=[TODAY.weekday()],
        threshold_method="INTERVALS", accent_mode="MANUAL", accents=["Z3"], accent_index=1.5,
        mesocycle_anchor=TODAY.isoformat())
    plan = run(monkeypatch, profile, Repository())
    pair = plan["days"][0]["sessions"]
    assert len(pair) == 2 and all(s["double_threshold"] for s in pair)
    assert all(planning_development.is_developmental(s) for s in pair)
    counts = planning_development.count([plan["days"][0]], start=TODAY, end=TODAY)
    assert counts["planned"] == 2
    assert_daily_limits(plan)


def test_old_valid_lock_keeps_its_dose_and_counts_once(monkeypatch):
    fixed_readiness(monkeypatch, 100.)
    profile = aerobic_profile(600., 3)
    repository = high_capacity_history()
    first = run(monkeypatch, profile, repository)
    locked = deepcopy(first["days"][0])
    for s in locked["sessions"]:
        s["execution_descriptor"].pop("developmental", None)
    plan = engine.generate_plan(repository, "athlete", profile, start_date=TODAY, now=NOW, locked_day=locked)
    assert plan["days"][0]["locked"]
    assert plan["days"][0]["sessions"][0]["blocks"] == locked["sessions"][0]["blocks"]
    counts = planning_development.count(plan["days"], start=TODAY, end=TODAY+timedelta(days=6))
    assert plan["allocation"]["development"]["segments"][0]["planned"] == counts["planned"]
    assert_daily_limits(plan)


def test_reserve_real_building_doses_then_fill_remaining_slots(monkeypatch):
    from apps.api import planning_search
    fixed_readiness(monkeypatch, 100.)
    profile = aerobic_profile(300., 2)
    original_goals = engine._goals
    def goals(*args, **kwargs):
        values, accents, cycle = original_goals(*args, **kwargs)
        values["Z1"]["target_weekly_q"] = 300.
        return values, accents, cycle
    monkeypatch.setattr(engine, "_goals", goals)
    gaps = {TODAY.isoformat(): {"Z1": .2}}
    policy = {"component_gap_first": True, "cycle_shortfalls": gaps}
    core = run(monkeypatch, {**profile, "_allocation_policy": {**policy, "building_pass": "CORE"}})
    reservations = planning_search.building_reservations(core, gaps)
    assert sum(map(len, reservations.values())) == 2
    assert all(planning_development.is_developmental(s) for d in core["days"] for s in d["sessions"])
    filled = run(monkeypatch, {**profile, "_allocation_policy": {
        **policy, "building_pass": "FILL", "reservation_methods": reservations}})
    sessions = [(day["date"], s) for day in filled["days"] for s in day["sessions"]]
    assert {(d, s["slot"]) for d, s in sessions if planning_development.is_developmental(s)} == {
        (d, r["slot"]) for d, records in reservations.items() for r in records}
    assert any(s["purpose"] == "MAINTENANCE" for _, s in sessions)
    assert 90 <= filled["allocation"]["segments"][0]["components"]["Z1"]["coverage_percent"] <= 110
    assert_daily_limits(filled)
