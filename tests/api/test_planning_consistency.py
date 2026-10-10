from copy import deepcopy

import pytest

from biathlon import planning_consistency as policy


def inputs():
    zones = ("Z1","Z2","Z3","Z4","Z5","STR")
    reference = {z:{"c40":20.,"b50":10.} for z in zones}
    goals = {z:{"target":140.,"reference":140.,"target_weekly_q":q,"progression":{}}
             for z,q in zip(zones,(240.,60.,30.,10.,5.,None))}
    profile = {"load_progression":{"enabled":True},"component_targets_weekly":{}}
    return goals,profile,reference


def test_reconciled_q_fits_adjacent_budget_without_exceeding_7_40_ceiling():
    goals,p,ref = inputs()
    result = policy.reconcile(goals,p,{"kind":"BUILD"},ref)
    assert result["Z1"]["target"] == 240  # No automatic cascade from upper zones.
    assert result["Z1"]["desired_weekly_q"] == 240
    assert 0 < result["Z1"]["target_weekly_q"] <= 240
    for g in result.values():
        assert g["consistency"]["projected_weekly_effective"] <= g["target"]+1e-6
        assert g["target_index"] <= 2


@pytest.mark.parametrize("case", ["manual","taper","recovery"])
def test_coherent_objectives_keep_manual_and_unloading_budgets(case):
    goals,p,ref = inputs()
    if case == "manual": p["component_targets_weekly"] = {"Z1":140}
    original = deepcopy(goals)
    result = policy.reconcile(goals,p,{"kind":"RECOVERY" if case=="recovery" else "BUILD"},ref,taper=case=="taper")
    assert result["Z1"]["target"] == original["Z1"]["target"]
    assert result["Z1"]["target_weekly_q"] < result["Z1"]["desired_weekly_q"]
    assert result["Z1"]["consistency"]["projected_weekly_effective"] <= 140+1e-6


def test_limited_history_does_not_inherit_an_enlarged_automatic_budget():
    goals,p,ref = inputs(); original = deepcopy(goals)
    assert policy.reconcile(goals,p,{"kind":"BUILD"},ref,limited=True) == original


def test_projection_uses_individual_continuous_capacity_not_historical_load():
    goals, profile, reference = inputs()
    reference["Z3"]["c40"] = 30.  # Leave enough 7/40 budget for all 420 direct minutes.
    for zone, goal in goals.items():
        goal["target_weekly_q"] = 420. if zone == "Z3" else 0.
    capacity = {f"Z{i}": 100. for i in range(1, 6)}
    result = policy.reconcile(goals, profile, {"kind": "BUILD"}, reference,
                              zone_tmax_minutes=capacity)
    assert result["Z2"]["consistency"]["projected_weekly_effective"] == 42.
    assert result["Z3"]["consistency"]["projected_weekly_effective"] == 420.
    assert result["Z4"]["consistency"]["projected_weekly_effective"] == 84.
    assert result["Z1"]["consistency"]["projected_weekly_effective"] == 0.
