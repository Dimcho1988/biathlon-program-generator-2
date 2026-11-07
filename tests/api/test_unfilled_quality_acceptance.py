"""An executable residual objective must survive an optional easy finish."""
from copy import deepcopy
from datetime import timedelta

import pytest

from apps.api import training_plan_engine as engine
from biathlon.component_load import calculate_component_load
from biathlon.constants import COMPONENTS
from biathlon import planning_allocation
from tests.api.test_load_progression import configured
from tests.api.test_readiness_adaptive_plan_v2 import fixed_readiness, assert_rolling_budgets, assert_readiness_dose
from tests.api.test_training_plan_engine import Repository, TODAY, NOW


def residual_fixture(monkeypatch, zone, *, readiness=100., preparation_q=20.):
    fixed_readiness(monkeypatch, readiness)
    profile = configured(age_years=30, training_experience_years=10,
                         strength_enabled=True, available_minutes=[240.] * 7)
    profile["planning_controls"].update(
        sessions_by_day=[2, 0, 0, 0, 0, 0, 0], sessions_per_week=7,
        accent_mode="MANUAL", accents=["Z4"], wave=[1., 1., 1., .78],
        mesocycle_anchor=(TODAY-timedelta(days=6)).isoformat(),
        threshold_method="AUTO", max_strength_sessions=2)
    repo = Repository()
    source = repo.envelope["snapshot_payload"]["load_history"]
    day = (TODAY-timedelta(days=3)).isoformat()
    direct = {z: 20. if z == "Z1" else 6. if z == "Z3" else 0.
              for z in COMPONENTS if z != "STR"}
    effective = calculate_component_load(direct)["effective"]
    activity = {"activity_ref": "partial-aerobic", "date": day, "sport": "Run",
        "duration_min": sum(direct.values()), "zones": [
            {"zone": z, "raw_time_min": direct[z], "equivalent_time_min": direct[z],
             "effective_load": effective[z]} for z in direct]}
    strength = {"activity_ref": "partial-strength", "date": day, "sport": "WeightTraining",
        "duration_min": 10., "strength_time_min": 10., "zones": [
            {"zone": z, "raw_time_min": 0., "equivalent_time_min": 0., "effective_load": 0.}
            for z in direct]}
    source["activities"].extend([activity, strength])
    repo.envelope["activities"].extend(
        {**deepcopy(a), "local_date": day} for a in [activity, strength])
    for row in source["daily"]:
        if row["date"] == day:
            row["effective_load"] = effective[row["zone"]]
    for row in source["strength"]["daily"]:
        if row["date"] == day:
            row.update(effective_load=10., real_time_min=10., equivalent_time_min=10.)

    original_goals = engine._goals
    def goals(*args, **kwargs):
        values, accents, cycle = original_goals(*args, **kwargs)
        # Known, separate synthetic Q objectives and generous E ceilings
        # isolate selection after all genuine method/readiness gates.
        for component in ("Z1", "Z2", "Z3", "Z4", "Z5"):
            target = 20.+preparation_q if component == "Z1" else 70. if component == zone else 0.
            values[component].update(target_weekly_q=target, desired_weekly_q=target, target=2000.)
        values["STR"]["target"] = 60. if zone == "STR" else 10.
        return values, accents, cycle
    monkeypatch.setattr(engine, "_goals", goals)
    wanted = "END-THR-LONG-01" if zone == "Z3" else "ONFLOWS-STR-RUN-END-V1"
    methods = [m for m in engine.resolved_methods(profile)
               if m["id"] in {"RUN-REC-EASY-01", wanted}]
    monkeypatch.setattr(engine, "resolved_methods", lambda _: deepcopy(methods))
    return repo, profile


@pytest.mark.parametrize("zone", ["Z3", "STR"])
def test_partial_quality_is_selected_before_easy_work_closes_preparation_budget(monkeypatch, zone):
    repo, profile = residual_fixture(monkeypatch, zone)
    original = deepcopy(repo.envelope)
    plan = engine.generate_plan(repo, "athlete", profile, start_date=TODAY, now=NOW)
    sessions = plan["days"][0]["sessions"]
    assert sessions and sessions[0]["zone"] == zone
    row = plan["allocation"]["segments"][0]["components"][zone]
    assert 0 < row["actual_q"] < row["target_q"]
    assert row["planned_q"] > 0
    assert repo.envelope == original
    assert_rolling_budgets(plan)
    for session in sessions:
        assert_readiness_dose(session)


@pytest.mark.parametrize("zone", ["Z3", "STR"])
@pytest.mark.parametrize("readiness,preparation_q", [(30., 20.), (100., 5.)])
def test_residual_priority_cannot_bypass_readiness_or_preparation_budget(monkeypatch, zone, readiness, preparation_q):
    repo, profile = residual_fixture(monkeypatch, zone, readiness=readiness, preparation_q=preparation_q)
    # A manually fixed preparation budget cannot acquire the automatic easy reserve.
    profile["component_targets_weekly"] = {"Z1":20.+preparation_q}
    plan = engine.generate_plan(repo, "athlete", profile, start_date=TODAY, now=NOW)
    assert not any(s["zone"] == zone for day in plan["days"] for s in day["sessions"])
    assert_rolling_budgets(plan)


def test_future_strength_reserves_preparation_after_old_sessions_leave_rolling_quota(monkeypatch):
    repo, profile = residual_fixture(monkeypatch, "STR", preparation_q=17.)
    profile["planning_controls"].update(
        mesocycle_anchor=TODAY.isoformat(), sessions_by_day=[1, 0, 0, 1, 0, 0, 0])
    source = repo.envelope["snapshot_payload"]["load_history"]
    earlier = deepcopy(next(a for a in source["activities"] if a["activity_ref"] == "partial-strength"))
    earlier.update(activity_ref="earlier-strength", date=(TODAY-timedelta(days=6)).isoformat())
    source["activities"].append(earlier)
    repo.envelope["activities"].append({**deepcopy(earlier), "local_date": earlier["date"]})
    for row in source["strength"]["daily"]:
        if row["date"] == earlier["date"]:
            row.update(effective_load=10., real_time_min=10., equivalent_time_min=10.)
    plan = engine.generate_plan(repo, "athlete", profile, start_date=TODAY, now=NOW)
    first = plan["days"][0]
    later = plan["days"][3]
    assert first["sessions"] and all(s["zone"] == "Z1" for s in first["sessions"])
    assert any(s["zone"] == "STR" for s in later["sessions"])
    assert any(limit["code"] == "LONG_TERM_QUALITY_PREPARATION" and "STR" in limit["protected_components"]
               for s in first["sessions"] for limit in s["dose_evidence"]["limits"])
    assert_rolling_budgets(plan)


def test_strength_bounds_keep_overperformed_cycle_separate_from_next_cycle():
    report = planning_allocation.strength_feasibility([
        {"window_start": "2026-09-08", "window_end": "2026-09-14", "desired_q": 100.,
         "attainable_upper_bound_q": 160., "session_quota": 2, "quota_bound_below_target": False},
        {"window_start": "2026-09-15", "window_end": "2026-09-21", "desired_q": 100.,
         "attainable_upper_bound_q": 60., "session_quota": 2, "quota_bound_below_target": True},
    ])
    assert report["attainable_upper_bound_q"] == 220.
    assert report["desired_q"] == 200.
    assert report["target_coverage_upper_bound_q"] == 160.
    assert report["quota_bound_below_target"] is True
    assert report["other_constraints_may_reduce"] is True
