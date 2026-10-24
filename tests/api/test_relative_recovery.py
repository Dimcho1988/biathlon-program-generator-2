from copy import deepcopy
from datetime import timedelta

import pytest

from biathlon import load_progression, planning_controls, mesocycle_focus, planning_consistency
from biathlon.constants import COMPONENTS
from tests.api.test_load_progression import configured, observed
from tests.api.test_training_plan_engine import TODAY, NOW, reference_speed


def test_recovery_reduces_observed_q_and_e_once_and_preserves_stable_anchor():
    p = configured()
    _, source, rows = observed()
    ctx = load_progression.context(p, source, rows, TODAY)
    anchor = deepcopy(ctx["anchor"])
    day = TODAY + timedelta(days=21)
    state = planning_controls.resolve(p, day, "GENERAL_PREPARATION", ["Z3"])
    ctx["recovery_reference"] = {"usable": True, "recovery_start": day.isoformat(),
        "start_date": (day-timedelta(days=7)).isoformat(), "end_date": (day-timedelta(days=1)).isoformat(),
        "components": {z: {"weekly_q": 20., "weekly_effective": 200.} for z in COMPONENTS}}
    base = planning_controls.reference(rows, TODAY)
    goals = planning_controls.goals(p, state, base, {}, limited=False, taper_factor=1.)
    result = load_progression.apply(goals, p, state, ctx, day, "GENERAL_PREPARATION", False, False, 1., base)
    result = mesocycle_focus.cap_recovery(result, p, state, base)
    result = planning_consistency.reconcile(result, p, state, base)
    for z, goal in result.items():
        assert goal["target"] == pytest.approx(140.)
        if z != "STR":
            assert goal["target_weekly_q"] == pytest.approx(14.)
        assert goal["recovery_reference"]["basis"] == "COMPLETED_OBSERVED_WEEK"
    assert ctx["anchor"] == anchor


def test_planned_wave_has_one_mean_and_thirty_percent_recovery_from_previous_loading():
    p = configured()
    for zone in COMPONENTS:
        states = [planning_controls.resolve(p, TODAY+timedelta(days=7*i), "GENERAL_PREPARATION", ["Z3"]) for i in range(4)]
        factors = [load_progression.cycle_shape(p, s, zone) for s in states]
        assert sum(factors)/4 == pytest.approx(1.)
        assert factors[3] == pytest.approx(.7*factors[2])


def test_incomplete_recovery_history_cannot_be_used_as_zero_or_full_observed_week():
    p = configured()
    _, source, rows = observed()
    # Move the cycle to just before recovery; deliberately omit one completed day.
    p["planning_controls"]["mesocycle_anchor"] = (TODAY-timedelta(days=20)).isoformat()
    ctx = load_progression.context(p, source, [r for r in rows if r["date"] != (TODAY-timedelta(days=2)).isoformat()], TODAY)
    assert ctx["recovery_reference"]["complete"] is False
    assert ctx["recovery_reference"]["usable"] is False


def test_manual_targets_keep_their_explicit_recovery_gate():
    p = configured(component_targets_weekly={"Z3": 21.})
    _, source, rows = observed()
    ctx = load_progression.context(p, source, rows, TODAY)
    day = TODAY+timedelta(days=21)
    state = planning_controls.resolve(p, day, "GENERAL_PREPARATION", ["Z3"])
    base = planning_controls.reference(rows, TODAY)
    goals = planning_controls.goals(p, state, base, {}, limited=False, taper_factor=1.)
    expected = goals["Z3"]["target"]
    result = load_progression.apply(goals, p, state, ctx, day, "GENERAL_PREPARATION", False, False, 1., base)
    assert result["Z3"]["target"] == expected
    assert "recovery_reference" not in result["Z3"]


def test_partial_recovery_dose_does_not_spend_other_components_preparation(monkeypatch):
    from apps.api import training_plan_engine as engine
    from tests.api.test_long_term_scheduling import assert_segment_contract
    repo, _, _ = observed()
    body = configured(age_years=30, training_experience_years=10,
                      available_minutes=[300]*6+[0], building_fraction=.65)
    body["planning_controls"].update(mesocycle_anchor=(TODAY-timedelta(days=21)).isoformat(),
        sessions_per_week=11, sessions_by_day=[2,2,2,2,2,1,0], long_session_day=5,
        mixed_sessions_enabled=True)
    # Synthetic coach objectives isolate allocation from the progression
    # policy above. Keep real capacities, readiness, whole doses and Q/E gates.
    targets = {"Z1":400., "Z2":100., "Z3":80., "Z4":20., "Z5":0., "STR":0.}
    ceilings = {"Z1":1500., "Z2":800., "Z3":350., "Z4":120., "Z5":20., "STR":0.}
    original = engine._goals
    def goals(*args, **kwargs):
        values, accents, cycle = original(*args, **kwargs)
        for zone in targets:
            values[zone].update(target_weekly_q=targets[zone], desired_weekly_q=targets[zone], target=ceilings[zone])
        return values, accents, cycle
    monkeypatch.setattr(engine, "_goals", goals)
    monkeypatch.setattr(engine.model_service, "speed_view", reference_speed)
    plan = engine.generate_plan(repo, "athlete", body, start_date=TODAY, now=NOW)
    assert all(day["status"] == "TRAINING" for day in plan["days"][:6])
    assert plan["days"][6]["status"] == "UNAVAILABLE" and not plan["days"][6]["sessions"]
    assert plan["allocation"]["components"]["Z2"]["planned_q"] >= 90.
    long_day = plan["days"][5]["sessions"]
    assert len(long_day) == 1 and long_day[0]["zone"] == "Z1" and long_day[0]["total_minutes"] >= 60.
    assert_segment_contract(plan)
