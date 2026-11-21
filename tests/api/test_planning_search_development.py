"""Weekly dose coverage and real developmental frequency are joint objectives."""
from copy import deepcopy

import pytest

from apps.api import planning_search


def plan(*, coverage=100., building=0, target=3, minimum=2):
    return {"days": [], "allocation": {
        "components": {"Z3": {"target": 100., "actual": 0., "planned": coverage,
                                "remaining": max(0., 100.-coverage)}},
        "development": {"minimum_shortfall": max(0, minimum-building),
                        "target_shortfall": max(0, target-building)}}}


def test_real_frequency_precedes_rounding_within_the_accepted_band():
    exact_but_maintenance = plan(building=0)
    two = plan(coverage=95., building=2)
    three = plan(coverage=95., building=3)
    assert planning_search.score(two, {"Z3"}) > planning_search.score(exact_but_maintenance, {"Z3"})
    assert planning_search.score(three, {"Z3"}) > planning_search.score(two, {"Z3"})
    assert planning_search.score(plan(building=3), {"Z3"}) > planning_search.score(three, {"Z3"})


@pytest.mark.parametrize("coverage", [89., 111.])
def test_developmental_count_cannot_buy_a_worse_component_range_gap(coverage):
    within_band = plan(building=1)
    outside_band = plan(coverage=coverage, building=3)
    assert planning_search.score(within_band, {"Z3"}) > planning_search.score(outside_band, {"Z3"})


def test_fully_covered_maintenance_week_still_searches_for_loading_frequency():
    baseline = plan(building=0)
    calls = []

    def replay(policy):
        calls.append(policy)
        return plan(building=3)

    result = planning_search.improve(baseline, {}, replay)
    assert len(calls) == 1
    assert calls[0]["development_first"] is True
    assert calls[0]["component_gap_first"] is True
    assert result["allocation"]["development"]["minimum_shortfall"] == 0
    assert result["allocation"]["search"]["conclusion"] == "TARGETS_COVERED"
    assert baseline["allocation"]["development"]["minimum_shortfall"] == 2


def test_recovery_without_development_requirement_does_not_add_replays():
    baseline = plan(building=0, minimum=0, target=0)

    def unexpected_replay(_):
        pytest.fail("An exact, maintenance-only recovery week needs no developmental repair")

    result = planning_search.improve(baseline, {}, unexpected_replay)
    assert result["allocation"]["search"]["alternatives"] == 0
    assert result["allocation"]["search"]["development_minimum_met"] is True


def test_time_limit_reports_unresolved_frequency_without_claiming_impossibility(monkeypatch):
    monkeypatch.setattr(planning_search, "monotonic", lambda: 100.)
    baseline = plan(building=1)
    result = planning_search.improve(baseline, {}, lambda _: pytest.fail("No replay budget"), seconds_remaining=0.)
    report = result["allocation"]["search"]
    assert report["coverage_within_tolerance"] is True
    assert report["development_minimum_met"] is False
    assert report["development_target_met"] is False
    assert report["conclusion"] == "SEARCH_BUDGET_EXHAUSTED"
    assert report["hard_limit_proven"] is False


def test_checked_variants_cannot_report_completion_with_desired_third_session_missing():
    baseline = plan(building=2)
    result = planning_search.improve(baseline, {}, lambda _: deepcopy(baseline))
    report = result["allocation"]["search"]
    assert report["development_minimum_met"] is True
    assert report["development_target_met"] is False
    assert report["conclusion"] == "CHECKED_VARIANTS_EXHAUSTED"


def test_manual_target_conflict_is_not_reported_as_completed_even_at_100_percent():
    baseline = plan(building=3)
    baseline["allocation"]["components"]["STR"] = {
        "target": 10., "actual": 10., "planned": 0., "activity_status": "CONFLICT"}
    result = planning_search.improve(baseline, {}, lambda _: pytest.fail("No useful redistribution"))
    assert result["allocation"]["search"]["configuration_conflict"] is True
    assert result["allocation"]["search"]["conclusion"] == "CONFIGURATION_CONFLICT"
