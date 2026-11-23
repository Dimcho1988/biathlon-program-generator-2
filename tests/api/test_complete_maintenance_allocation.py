"""Residual objectives admit complete methods within the local 90–110% band."""
from copy import deepcopy

import pytest

from apps.api import training_plan_engine as engine
from biathlon import method_library, planning_development
from biathlon.constants import COMPONENTS
from tests.api.test_management_schedule import body, high_capacity_history, run
from tests.api.test_management_v2 import interval
from tests.api.test_readiness_adaptive_plan_v2 import fixed_readiness
from tests.api.test_training_plan_engine import TODAY


METHOD = "END-VO2-TREF-01-Z5"
ALTERNATE = "COACH-ALTERNATE-Z5"


def generate(monkeypatch, target, *, key_sessions=0, alternate=False):
    fixed_readiness(monkeypatch, 100.)
    profile = body(sessions_per_week=7, sessions_by_day=[1]*7,
        mesocycle_anchor=TODAY.isoformat(), accent_mode="MANUAL", accents=["Z5"],
        mixed_sessions_enabled=False)
    # The coach explicitly permits one or two 3.5-minute repetitions. The
    # bounded full method is two repetitions, its maintenance dose one.
    profile.update(max_key_sessions_per_week=key_sessions,
        interval_profiles=[interval("Z5", continuous_capacity_min=20.,
            work_seconds=210., min_repetitions=1, max_repetitions=2)],
        component_targets_weekly={z: 500. if z == "Z1" else target if z == "Z5" else 0.
                                  for z in COMPONENTS},
        _allocation_policy={"component_gap_first": True})
    methods = [m for m in engine.resolved_methods(profile) if m["id"] == METHOD]
    assert len(methods) == 1
    if alternate:
        other = deepcopy(methods[0])
        other["id"] = ALTERNATE
        other["interval_profile"]["work_seconds"] = 180.
        methods.append(other)
    monkeypatch.setattr(engine, "resolved_methods", lambda _: deepcopy(methods))
    monkeypatch.setattr(method_library, "executable_methods", lambda *a, **kw: [])
    original = engine._goals

    def goals(*args, **kwargs):
        values, accents, cycle = original(*args, **kwargs)
        for zone, prescribed in profile["component_targets_weekly"].items():
            values[zone].update(target_weekly_q=prescribed, desired_weekly_q=prescribed)
        return values, accents, cycle

    monkeypatch.setattr(engine, "_goals", goals)
    return run(monkeypatch, profile, high_capacity_history())


def sessions(plan):
    return [session for day in plan["days"] for session in day["sessions"]]


def work(session):
    return [block for block in session["blocks"] if block["kind"] == "WORK"]


@pytest.mark.parametrize("target,coverage,status", [
    (17.1/1.05, 105., "WITHIN_RANGE"),
    (18., 95., "WITHIN_RANGE"),
    (17.1/.875, 87.5, "BELOW_RANGE"),
])
def test_whole_maintenance_can_cross_100_or_leave_an_unfillable_remainder(
        monkeypatch, target, coverage, status):
    plan = generate(monkeypatch, target)
    selected = sessions(plan)
    assert len(selected) == 3
    assert all(s["purpose"] == "MAINTENANCE" for s in selected)
    assert all([b["duration_min"] for b in work(s)] == [3.5] for s in selected)
    row = plan["allocation"]["segments"][0]["components"]["Z5"]
    assert row["target"] == pytest.approx(target, abs=.001)
    assert row["planned"] == pytest.approx(17.1)
    assert row["coverage_percent"] == pytest.approx(coverage)
    assert row["coverage_status"] == status
    assert row["planned"] <= row["upper_acceptable"]
    assert all(not day["sessions"] for day in plan["days"][3:])
    if coverage > 100.:
        before_last = sum(s["direct_equivalent_minutes"]["Z5"] for s in selected[:-1])
        assert selected[-1]["direct_equivalent_minutes"]["Z5"] > target-before_last
        assert any(limit["code"] == "UPPER_WHOLE_Q_DOSE_IF_ADMISSIBLE"
                   for limit in selected[-1]["dose_evidence"]["limits"])
    else:
        # Neither 90% nor 100% permits inventing a shorter repetition.
        assert row["remaining"] > 0.
        assert any(r["code"] == "DIRECT_Q_PROGRESSION_BUDGET"
                   for day in plan["days"][3:] for r in day["rejected_alternatives"])


def test_unfitting_preferred_method_falls_back_to_another_complete_method(monkeypatch):
    plan = generate(monkeypatch, 15.5, alternate=True)
    selected = sessions(plan)
    assert [s["method_id"] for s in selected] == [METHOD, METHOD, ALTERNATE]
    assert [[b["duration_min"] for b in work(s)] for s in selected] == [[3.5], [3.5], [3.]]
    assert any(r["method_id"] == METHOD and r["code"] == "DIRECT_Q_PROGRESSION_BUDGET"
               for r in plan["days"][2]["rejected_alternatives"])
    row = plan["allocation"]["segments"][0]["components"]["Z5"]
    assert row["coverage_percent"] == pytest.approx(105.071, abs=.001)
    assert row["coverage_status"] == "WITHIN_RANGE"
    assert all(not day["sessions"] for day in plan["days"][3:])


def test_building_core_is_preserved_while_maintenance_fills_the_residual(monkeypatch):
    plan = generate(monkeypatch, 28.5, key_sessions=2)
    selected = sessions(plan)
    assert [s["purpose"] for s in selected] == ["BUILDING", "MAINTENANCE", "BUILDING"]
    assert [len(work(s)) for s in selected] == [2, 1, 2]
    assert sum(planning_development.is_developmental(s) for s in selected) == 2
    assert plan["allocation"]["development"]["minimum_shortfall"] == 0
    assert plan["allocation"]["development"]["target_shortfall"] == 0
    row = plan["allocation"]["segments"][0]["components"]["Z5"]
    assert row["coverage_percent"] == 100.
    assert row["coverage_status"] == "WITHIN_RANGE"
