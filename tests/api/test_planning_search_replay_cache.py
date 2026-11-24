"""Reuse exact causal trials and repair newly exposed preparation deficits."""
from copy import deepcopy
import json

import pytest

from apps.api import planning_search


FIRST_POLICY = {"optional_easy_fraction": 0., "balance_complete_doses": False,
                "component_gap_first": True, "development_first": True}


def repeated_plan(coverage=50.):
    return {"days": [{"date": "2026-11-24", "sessions": [{
        "method_id": "COMPLETE", "sport": "Run", "zone": "Z3", "slot": 1,
        "main_work_minutes": 12., "blocks": [
            {"kind": "WORK", "zone": "Z3", "duration_min": 6.},
            {"kind": "RECOVERY", "zone": "Z1", "duration_min": 2.},
            {"kind": "WORK", "zone": "Z3", "duration_min": 6.}],
        "dose_evidence": {"daily_intensity": {"used_fraction": .6},
                          "readiness_dose_factor": 1.}}]}],
        "parameters": {"allocation_policy": deepcopy(FIRST_POLICY)},
        "allocation": {"components": {"Z3": {"target": 100., "actual": 0., "planned": coverage}},
                       "development": {"minimum_shortfall": 0, "target_shortfall": 1}}}


def policy_key(policy):
    return json.dumps(policy, sort_keys=True, separators=(",", ":"))


def test_duplicate_refit_reuses_exact_result_without_changing_the_chosen_plan():
    baseline = repeated_plan()
    original = deepcopy(baseline)
    calls, returned = [], []

    def replay(policy):
        calls.append(deepcopy(policy))
        candidate = repeated_plan(75.)
        candidate["parameters"]["allocation_policy"] = deepcopy(policy)
        returned.append(candidate)
        return candidate

    result = planning_search.improve(baseline, {}, replay)
    assert result is returned[0]  # Exact ties retain the first stable plan.
    assert len(calls) == len({policy_key(p) for p in calls}) == 8
    assert calls[0] == FIRST_POLICY  # A matching baseline policy is not pre-seeded.
    assert result["allocation"]["search"]["alternatives"] == 8
    assert result["allocation"]["search"]["reservation_replays"] == 1
    saved = deepcopy(result)
    saved["allocation"].pop("search")
    assert saved == repeated_plan(75.)  # Whole work, role/readiness evidence and targets survive.
    assert baseline == original
    assert all("search" not in candidate["allocation"] for candidate in returned[1:])


def test_replay_cache_is_local_to_each_search():
    calls = []

    def replay(policy):
        calls.append(policy_key(policy))
        candidate = repeated_plan(75.)
        candidate["parameters"]["allocation_policy"] = deepcopy(policy)
        return candidate

    first = planning_search.improve(repeated_plan(), {}, replay)
    first_calls = len(calls)
    second = planning_search.improve(repeated_plan(), {}, replay)
    assert first is not second
    assert first_calls == 8 and len(calls) == 16
    assert calls[:first_calls] == calls[first_calls:]


def test_cached_refit_needs_neither_fresh_time_nor_another_unique_attempt(monkeypatch):
    elapsed, calls = [0.], []
    monkeypatch.setattr(planning_search, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(planning_search, "MAX_UNIQUE_REPLAYS", 8)

    def replay(policy):
        elapsed[0] += 10.
        calls.append(deepcopy(policy))
        candidate = repeated_plan(75.)
        candidate["parameters"]["allocation_policy"] = deepcopy(policy)
        return candidate

    result = planning_search.improve(repeated_plan(), {}, replay,
        seconds_remaining=90., estimated_trial_seconds=10.)
    assert len(calls) == 8 and elapsed[0] == 80.
    assert result["allocation"]["search"]["conclusion"] == "CHECKED_VARIANTS_EXHAUSTED"
    assert result["allocation"]["search"]["hard_limit_proven"] is False


@pytest.mark.parametrize("target_shortfall,expected_calls", [(0, 6), (1, 7)])
def test_known_inactive_optional_finish_skips_only_its_equivalent_policies(target_shortfall, expected_calls):
    baseline = repeated_plan()
    baseline["allocation"]["development"]["target_shortfall"] = target_shortfall
    baseline["parameters"]["allocation_policy_capabilities"] = {"optional_easy_fraction": False}
    original, calls = deepcopy(baseline), []

    def replay(policy):
        calls.append(deepcopy(policy))
        candidate = deepcopy(baseline)
        candidate["allocation"]["components"]["Z3"]["planned"] = 75.
        candidate["parameters"]["allocation_policy"] = deepcopy(policy)
        return candidate

    result = planning_search.improve(baseline, {}, replay)
    effective = [{key: value for key, value in policy.items() if key != "optional_easy_fraction"}
                 for policy in calls]
    assert len(calls) == len({policy_key(policy) for policy in effective}) == expected_calls
    assert all("optional_easy_fraction" in policy for policy in calls)  # Original replay inputs are unchanged.
    assert result["parameters"]["allocation_policy"]["optional_easy_fraction"] == 0.
    assert result["allocation"]["components"]["Z3"]["planned"] == 75.
    assert result["allocation"]["development"] == original["allocation"]["development"]
    if target_shortfall:
        # Do not normalize other defaults: explicit development-first and its
        # absence remain distinct even when the optional tail is inactive.
        assert any(policy.get("development_first") is True for policy in calls)
        assert any("development_first" not in policy and policy.get("component_gap_first") is True
                   and policy.get("balance_complete_doses") is False for policy in calls)
    assert baseline == original


@pytest.mark.parametrize("capability", [True, None, 0, "false", "MISSING"])
def test_enabled_or_unknown_optional_finish_keeps_unselected_mix_alternatives(capability):
    baseline = repeated_plan()
    if capability != "MISSING":
        baseline["parameters"]["allocation_policy_capabilities"] = {"optional_easy_fraction": capability}
    calls = []

    def replay(policy):
        calls.append(deepcopy(policy))
        candidate = deepcopy(baseline)
        if policy.get("optional_easy_fraction") == .5:
            candidate["allocation"]["components"]["Z3"]["planned"] = 95.
            candidate["days"][0]["sessions"][0]["method_id"] = "ENABLED-MIX"
        candidate["parameters"]["allocation_policy"] = deepcopy(policy)
        return candidate

    result = planning_search.improve(baseline, {}, replay)
    assert baseline["days"][0]["sessions"][0]["method_id"] == "COMPLETE"
    assert result["days"][0]["sessions"][0]["method_id"] == "ENABLED-MIX"
    assert result["allocation"]["components"]["Z3"]["planned"] == 95.
    assert {0., .5, 1.} <= {policy["optional_easy_fraction"] for policy in calls}


def preparation_plan(z3=80., z4=50.):
    rows = {z: {"basis": "DIRECT_Q", "target": 100., "actual": 0., "planned": value}
            for z, value in (("Z1", 100.), ("Z3", z3), ("Z4", z4))}
    return {"days": [{"date": "2026-11-24", "sessions": [{
        "method_id": "DONOR", "sport": "Run", "zone": "Z3", "main_work_minutes": 70.,
        "dose_evidence": {"minimum_structure_work_minutes": 14.},
        "direct_equivalent_minutes": {"Z1": 50., "Z3": 20.}}]}],
        "lookahead_days": [{"date": "2026-11-29", "sessions": [{
            "method_id": "QUALITY", "sport": "Run", "zone": "Z4", "main_work_minutes": 10.}]}],
        "parameters": {"allocation_policy": {}},
        "allocation": {"components": deepcopy(rows), "segments": [{
            "window_start": "2026-11-24", "window_end": "2026-11-30", "components": deepcopy(rows)}],
            "development": {"minimum_shortfall": 0, "target_shortfall": 0}}}


def test_later_best_gets_the_same_existing_complete_preparation_repair():
    baseline, later = preparation_plan(), preparation_plan(100., 80.)
    original, calls = deepcopy(baseline), []
    assert planning_search.preparation_repair_policy(baseline) is None
    assert planning_search.preparation_repair_policy(later)["easy_caps"] == {"2026-11-24": 14.}

    def replay(policy):
        calls.append(deepcopy(policy))
        if policy.get("easy_cap_zones") == ["Z3"]:
            assert policy["easy_caps"] == {"2026-11-24": 14.}
            candidate = preparation_plan(90., 95.)
        else:
            candidate = deepcopy(later if policy.get("optional_easy_fraction") == .5 else baseline)
        candidate["parameters"]["allocation_policy"] = deepcopy(policy)
        return candidate

    result = planning_search.improve(baseline, {}, replay)
    assert result["allocation"]["components"]["Z3"]["planned"] == 90.
    assert result["allocation"]["components"]["Z4"]["planned"] == 95.
    assert result["allocation"]["development"] == baseline["allocation"]["development"]
    assert result["allocation"]["search"]["coverage_within_tolerance"] is True
    assert len(calls) == len({policy_key(p) for p in calls}) <= planning_search.MAX_UNIQUE_REPLAYS
    assert baseline == original


def test_new_best_repairs_share_the_existing_finite_replay_budget():
    calls = []

    def replay(policy):
        calls.append(deepcopy(policy))
        # Each fresh alternative improves a deficit and exposes the same
        # valid donor under another policy; repairs still cannot recurse.
        candidate = preparation_plan(100., 20. + len(calls))
        candidate["parameters"]["allocation_policy"] = deepcopy(policy)
        return candidate

    result = planning_search.improve(preparation_plan(100., 20.), {}, replay)
    assert len(calls) == len({policy_key(p) for p in calls}) == planning_search.MAX_UNIQUE_REPLAYS == 12
    report = result["allocation"]["search"]
    assert report["alternatives"] == 12
    assert report["reservation_replays"] == sum("reservation_methods" in p for p in calls)
    assert report["conclusion"] == "SEARCH_BUDGET_EXHAUSTED"
    assert report["hard_limit_proven"] is False
