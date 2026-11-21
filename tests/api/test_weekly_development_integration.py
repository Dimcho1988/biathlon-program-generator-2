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
