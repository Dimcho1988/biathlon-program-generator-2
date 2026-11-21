from copy import deepcopy

import pytest

from biathlon import planning_consistency as policy


def inputs():
    zones = ("Z1","Z2","Z3","Z4","Z5","STR")
    reference = {z:{"c40":20.,"b50":10.} for z in zones}
    goals = {z:{"target":140.,"reference":140.,"target_weekly_q":q,"progression":{}}
             for z,q in zip(zones,(240.,60.,30.,10.,5.,None))}
    profile = {"load_progression":{"enabled":True},"component_targets_weekly":{}, "strength_enabled":True}
    return goals,profile,reference


def test_projection_discloses_e_without_shrinking_the_prescribed_q():
    goals,p,ref = inputs()
    result = policy.reconcile(goals,p,{"kind":"BUILD"},ref)
    assert result["Z1"]["target"] == 140
    assert result["Z1"]["desired_weekly_q"] == 240
    assert result["Z1"]["target_weekly_q"] == 240
    for g in result.values():
        assert not g["consistency"]["projection_is_limit"]
        assert g["consistency"]["q_scale"] == 1.


@pytest.mark.parametrize("case", ["manual","taper","recovery"])
def test_coherent_objectives_keep_manual_and_unloading_budgets(case):
    goals,p,ref = inputs()
    if case == "manual": p["component_targets_weekly"] = {"Z1":140}
    original = deepcopy(goals)
    result = policy.reconcile(goals,p,{"kind":"RECOVERY" if case=="recovery" else "BUILD"},ref,taper=case=="taper")
    assert result["Z1"]["target"] == original["Z1"]["target"]
    assert result["Z1"]["target_weekly_q"] == original["Z1"]["target_weekly_q"]
    assert result["Z1"]["consistency"]["projected_weekly_effective"] > 140.
    assert not result["Z1"]["consistency"]["projection_is_limit"]


def test_limited_history_does_not_inherit_an_enlarged_automatic_budget():
    goals,p,ref = inputs(); original = deepcopy(goals)
    assert policy.reconcile(goals,p,{"kind":"BUILD"},ref,limited=True) == original


def test_projection_uses_individual_continuous_capacity_not_historical_load():
    goals, profile, reference = inputs()
    # Leave room for direct Q and all downward participation, so only Tmax
    # determines the adjacent extras in this test.
    for zone in reference:
        reference[zone]["c40"] = 100.
    for zone, goal in goals.items():
        goal["target_weekly_q"] = 420. if zone == "Z3" else 0.
    capacity = {f"Z{i}": 100. for i in range(1, 6)}
    result = policy.reconcile(goals, profile, {"kind": "BUILD"}, reference,
                              zone_tmax_minutes=capacity)
    assert result["Z2"]["consistency"]["projected_weekly_effective"] == 798.
    assert result["Z3"]["consistency"]["projected_weekly_effective"] == 420.
    assert result["Z4"]["consistency"]["projected_weekly_effective"] == 84.
    assert result["Z1"]["consistency"]["projected_weekly_effective"] == 756.


@pytest.mark.parametrize("mode", ["automatic", "legacy", "limited", "recovery"])
def test_disabled_strength_excludes_automatic_target_without_erasing_history(mode):
    goals, profile, reference = inputs()
    profile["strength_enabled"] = False
    if mode == "legacy":
        profile.pop("load_progression")
    before = deepcopy(reference)
    result = policy.reconcile(goals, profile, None if mode == "legacy" else
                              {"kind": "RECOVERY" if mode == "recovery" else "BUILD"},
                              reference, limited=mode == "limited")
    strength = result["STR"]
    assert strength["target"] == strength["target_weekly_q"] == strength["desired_weekly_q"] == 0.
    assert strength["reference"] == 140.
    assert strength["activity"]["status"] == "DISABLED"
    assert strength["activity"]["automatic_target_before_exclusion"] == 140.
    assert strength["target_index"] == pytest.approx(1/3)
    assert not strength["development"]
    assert reference == before


def test_disabled_strength_preserves_explicit_goal_and_explains_the_conflict():
    goals, profile, reference = inputs()
    profile.update(strength_enabled=False, component_targets_weekly={"STR": 140.})
    original = deepcopy(goals["STR"])
    strength = policy.reconcile(goals, profile, {"kind": "BUILD"}, reference)["STR"]
    assert strength["target"] == original["target"]
    assert strength["target_weekly_q"] is None
    assert strength["activity"]["status"] == "CONFLICT"
    assert strength["activity"]["conflict"] == policy.configuration_conflicts(profile)[0]
    assert strength["activity"]["conflict"]["code"] == "DISABLED_COMPONENT_TARGET"
    profile["strength_enabled"] = True
    assert not policy.configuration_conflicts(profile)


def test_inactive_objective_keeps_completed_work_but_does_not_distort_coverage():
    from datetime import timedelta
    from biathlon import planning_allocation
    from tests.api.test_training_plan_engine import TODAY

    goals, profile, reference = inputs()
    profile["strength_enabled"] = False
    goals = policy.reconcile(goals, profile, {"kind": "BUILD"}, reference)
    windows = {TODAY + timedelta(days=i): deepcopy(goals) for i in range(7)}
    actual = [{"date": TODAY.isoformat(), "zone": "STR", "effective_load": 42.}]
    components = planning_allocation.objectives(windows, actual, actual, {"activities": []}, [])
    strength = components["STR"]
    assert strength["target"] == 0. and strength["actual"] == 42.
    assert strength["activity_status"] == "DISABLED"
    assert strength["coverage_status"] == "NOT_REQUIRED"
    assert strength["prescription_remaining"] == 0.
    assert "STR" not in dict(planning_allocation.objective_rows({"components": components}))
    assert planning_allocation.gap_score([("STR", strength)]) == (0., 0., 0.)
    components["Z1"]["actual"] = None
    assert "Z1" in dict(planning_allocation.objective_rows({"components": components}))
    assert planning_allocation.assess_component(components["Z1"])["coverage_status"] == "UNKNOWN"


def test_disabled_manual_goal_stays_in_allocation_objectives_as_a_conflict():
    from datetime import timedelta
    from biathlon import planning_allocation
    from tests.api.test_training_plan_engine import TODAY

    goals, profile, reference = inputs()
    profile.update(strength_enabled=False, component_targets_weekly={"STR": 140.})
    goals = policy.reconcile(goals, profile, {"kind": "BUILD"}, reference)
    windows = {TODAY + timedelta(days=i): deepcopy(goals) for i in range(7)}
    components = planning_allocation.objectives(windows, [], [], {"activities": []}, [])
    strength = components["STR"]
    assert strength["target"] == 140.
    assert strength["activity_status"] == "CONFLICT"
    assert strength["coverage_status"] == "BELOW_RANGE"
    assert "STR" in dict(planning_allocation.objective_rows({"components": components}))


@pytest.mark.parametrize("progression_enabled", [False, True])
def test_weekly_plan_and_outlook_share_disabled_strength_goal(monkeypatch, progression_enabled):
    from apps.api import training_plan_engine as engine
    from apps.api.management_schemas import PlanningControls
    from tests.api.test_training_plan_engine import Repository, TODAY, NOW, profile, reference_speed

    monkeypatch.setattr(engine.model_service, "speed_view", reference_speed)
    body = profile(strength_enabled=False, reentry_days=0,
                   planning_controls=PlanningControls(mesocycle_anchor=TODAY).model_dump(mode="json"),
                   load_progression={"enabled": True} if progression_enabled else {})
    repository = Repository()
    original = deepcopy(repository.envelope)
    plan = engine.generate_plan(repository, "athlete", body, start_date=TODAY, now=NOW)
    assert plan["long_term"]["baseline"]["STR"]["c40"] > 0.
    for week in plan["long_term"]["weeks"]:
        assert week["components"]["STR"]["target_weekly_effective"] == 0.
        assert week["components"]["STR"]["target_weekly_q"] == 0.
    for segment in plan["allocation"]["segments"]:
        assert segment["components"]["STR"]["target"] == 0.
        assert segment["components"]["STR"]["objective_active"] is False
    assert repository.envelope == original
