"""Daily doses accumulate across methods, sports, executions and saved plans."""
from copy import deepcopy

import pytest

from biathlon import daily_intensity as daily
from apps.api import training_plan_engine as engine
from tests.api.test_management_schedule import body, run, high_capacity_history
from tests.api.test_training_plan_engine import TODAY, NOW


def block(zone, minutes, part=None, kind="WORK"):
    return {"zone": zone, "duration_min": minutes, "kind": kind, "combination_part": part}


def contract(*refs, building=False):
    return {"version": daily.VERSION, "building": building, "references": [
        {"part": part, "zones": [zone], "minutes": minutes} for part, zone, minutes in refs]}


EMPTY = {"used": 0., "actual": 0., "building": False, "double": False}


def test_two_parts_share_one_maintenance_budget_and_easy_parts_are_not_counted_twice():
    c = contract((1, "Z5", 20.), (2, "Z4", 40.))
    parts = [block("Z5", 6, 1), block("Z4", 12, 2), block("Z1", 90), block("Z4", 4, kind="RECOVERY")]
    assert daily.usage(parts, c["references"]) == pytest.approx(.6)
    assert daily.check(parts, c, EMPTY)
    assert not daily.check(parts + [block("Z5", .5, 1)], c, EMPTY)
    assert daily.check([block("Z1", 120)], c, {**EMPTY, "used": 1.2})


def test_reported_three_session_pattern_exceeds_budget_even_with_maintenance_labels():
    patterns = [
        ([block("Z5", 9.5, 1), block("Z4", 26.4, 2)], contract((1, "Z5", 16.25), (2, "Z4", 44.))),
        ([block("Z5", 10.8, 1), block("Z3", 31.5, 2)], contract((1, "Z5", 18.), (2, "Z3", 53.09135))),
        ([block("Z4", 21.695, 1), block("Z2", 51, 2)], contract((1, "Z4", 44.))),
    ]
    sessions = [{"blocks": b, "purpose": "MAINTENANCE", "sport": s,
                 "dose_evidence": {"daily_intensity": c}}
                for (b, c), s in zip(patterns, ["NordicSki", "Run", "NordicSki"])]
    result = daily.summary(EMPTY, sessions)
    assert result["used_fraction"] == pytest.approx(2.8711, abs=.001)
    assert result["limit_fraction"] == .6
    assert not daily.check(*patterns[0], EMPTY)


def test_prior_ski_work_consumes_running_budget_and_actual_q_is_not_effective_load():
    c = contract((None, "Z4", 40.))
    sessions = [{"blocks": [block("Z4", 20)], "purpose": "MAINTENANCE", "sport": "NordicSki",
                 "dose_evidence": {"daily_intensity": c}}]
    ledger = daily.ledger([], sessions, {}, .65)
    assert not daily.check([block("Z4", 8)], c, ledger)
    activities = [{"sport": "Run", "zones": [{"zone": "Z3", "raw_time_min": 10,
        "equivalent_time_min": 6.5, "effective_load": 500.}]}]
    assert daily.actual_usage(activities, {"Run": {"minutes": {"Z3": 50.}}}, .65) == pytest.approx(.2)
    assert daily.actual_usage(activities, {}, .65) is None


def test_explicit_double_threshold_reserves_day_and_cannot_be_followed_by_extra_quality():
    c = {**contract(), "building": True, "exception": "EXPLICIT_DOUBLE_THRESHOLD"}
    parts = [block("Z3", 30)]
    assert daily.check(parts, c, EMPTY)
    assert not daily.check(parts, c, {**EMPTY, "used": .2})
    pair = [{"blocks": parts, "double_threshold": True, "double_threshold_part": i,
             "purpose": "BUILDING", "dose_evidence": {"daily_intensity": c}} for i in (1, 2)]
    after = daily.ledger([], pair, {}, .65)
    assert after["used"] == 1.
    assert not daily.check([block("Z4", 1)], contract((None, "Z4", 40.)), after)


def test_missing_or_old_contract_does_not_release_a_new_budget():
    s = {"blocks": [block("Z5", 10)], "purpose": "MAINTENANCE", "dose_evidence": {}}
    assert daily.ledger([], [s], {}, .65)["used"] is None
    assert not daily.check(s["blocks"], contract(), EMPTY)


def test_easy_building_wrapper_cannot_raise_maintenance_quality_budget():
    s = {"blocks": [block("Z4", 20), block("Z2", 60)], "purpose": "BUILDING",
         "dose_evidence": {"daily_intensity": contract((None, "Z4", 40.))}}
    before = daily.ledger([], [s], {}, .65)
    assert not before["building"]
    assert daily.summary(EMPTY, [s])["limit_fraction"] == .6
    assert not daily.check([block("Z4", 8)], contract((None, "Z4", 40.)), before)


def test_legacy_combination_restores_each_original_method_reference():
    s = {"blocks": [block("Z5", 6, 1), block("Z4", 12, 2)], "purpose": "MAINTENANCE",
         "dose_evidence": {"combined_capacities": [
             {"capacity_minutes": 20.}, {"capacity_minutes": 40.}],
             "combined_budgets": [{"zone": z, "role": "MAINTENANCE", "full_role_fraction": .6,
                                   "method_id": z} for z in ("Z5", "Z4")]}}
    restored = daily.saved_contract(s)
    assert daily.usage(s["blocks"], restored["references"]) == pytest.approx(.6)
    assert daily.check(s["blocks"], restored, EMPTY)
    s["dose_evidence"].pop("combined_capacities")
    assert daily.saved_contract(s) is None


@pytest.mark.parametrize("over_budget", [False, True])
def test_saved_maintenance_day_is_revalidated_before_preservation(monkeypatch, over_budget):
    from tests.api.test_planning_continuity import scenario, adaptive_sessions
    p, repo = scenario(monkeypatch)
    plan = engine.generate_plan(repo, "athlete", p, start_date=TODAY, now=NOW)
    assert adaptive_sessions(plan)
    locked = deepcopy(plan["days"][0])
    for s in locked["sessions"]:
        current = s["dose_evidence"].pop("daily_intensity")
        restored = daily.saved_contract(s)
        assert restored is not None
        assert daily.usage(s["blocks"], restored["references"]) == pytest.approx(
            daily.usage(s["blocks"], current["references"]))
        if over_budget:
            # Same persisted method evidence, excessive direct work.
            for b in daily.high_work(s["blocks"]):
                b["duration_min"] *= 10
            s["total_minutes"] = sum(b["duration_min"] for b in s["blocks"])
    refreshed = engine.generate_plan(repo, "athlete", p, start_date=TODAY, now=NOW, locked_day=locked)
    day = refreshed["days"][0]
    if over_budget:
        assert day["status"] == "REVIEW_REQUIRED"
        assert not day["sessions"]
        assert any(r["code"] == "DAILY_INTENSITY_BUDGET" for r in day["rejected_alternatives"])
    else:
        assert day["locked"]
        assert day["sessions"][0]["blocks"] == locked["sessions"][0]["blocks"]


def test_generated_days_obey_shared_budget_and_keep_multiple_easy_sessions(monkeypatch):
    p = body(sessions_per_week=21, sessions_by_day=[3]*7)
    p["max_key_sessions_per_week"] = 0
    plan = run(monkeypatch, p)
    assert plan["summary"]["sessions"] > 7
    for day in plan["days"]:
        budget = day["daily_intensity_budget"]
        assert budget["used_fraction"] is not None
        assert budget["used_fraction"] <= budget["limit_fraction"] + daily.EPS
        for session in day["sessions"]:
            assert sum(b["duration_min"] for b in session["blocks"]) == pytest.approx(session["total_minutes"], abs=.002)


@pytest.mark.parametrize("zone,capacity,seconds,minimum,maximum,ratio,expected_code,session_count", [
    ("Z4", 5., 180, 2, 6, 2., "INSUFFICIENT_DOSE_BUDGET", 0),
    ("Z5", 20., 30, 6, 20, 2.5, "DAILY_INTENSITY_BUDGET", 1),
])
def test_method_role_limit_is_distinct_from_shared_daily_limit(
        monkeypatch, zone, capacity, seconds, minimum, maximum, ratio, expected_code, session_count):
    from biathlon import method_library
    from tests.api.test_management_v2 import interval
    from tests.api.test_readiness_adaptive_plan_v2 import fixed_readiness

    fixed_readiness(monkeypatch, 100.)
    p = body(sessions_per_week=2, sessions_by_day=[2, 0, 0, 0, 0, 0, 0],
        mesocycle_anchor=TODAY.isoformat(), accent_mode="MANUAL", accents=[zone],
        mixed_sessions_enabled=False)
    p.update(max_key_sessions_per_week=0,
        interval_profiles=[interval(zone, continuous_capacity_min=capacity, work_seconds=seconds,
            min_repetitions=minimum, max_repetitions=maximum, total_capacity_ratio=ratio)],
        component_targets_weekly={"Z1": 500., zone: 100.},
        _allocation_policy={"balance_complete_doses": False})
    identifier = f"END-VO2-TREF-01-{zone}"
    methods = [method for method in engine.resolved_methods(p) if method["id"] == identifier]
    assert len(methods) == 1
    monkeypatch.setattr(engine, "resolved_methods", lambda _: deepcopy(methods))
    monkeypatch.setattr(method_library, "executable_methods", lambda *args, **kwargs: [])

    plan = run(monkeypatch, p, high_capacity_history())

    day = plan["days"][0]
    assert len(day["sessions"]) == session_count
    codes = {row["code"] for row in day["rejected_alternatives"] if row["method_id"] == identifier}
    assert expected_code in codes
    if session_count == 0:
        # The whole 2 × 3-minute method cannot fit its 3.9-minute role dose;
        # there is no prior intensity to exhaust the shared daily budget.
        assert day["daily_intensity_budget"]["used_fraction"] == 0.
        assert "DAILY_INTENSITY_BUDGET" not in codes
    else:
        # A complete 6-minute maintenance dose fits, then fills the shared day.
        assert day["sessions"][0]["main_work_minutes"] == 6.
        assert day["daily_intensity_budget"]["used_fraction"] == pytest.approx(.6)
        assert "INSUFFICIENT_DOSE_BUDGET" not in codes


def test_nonbinding_single_session_days_keep_same_prescriptions(monkeypatch):
    p = body(sessions_per_week=7, sessions_by_day=[1]*7)
    p["max_key_sessions_per_week"] = 0
    repo = high_capacity_history()
    guarded = run(monkeypatch, p, repo)
    monkeypatch.setattr(daily, "check", lambda *args, **kwargs: True)
    original = run(monkeypatch, p, repo)
    def prescribed(plan):
        return [[(s["method_id"], s["sport"], s["total_minutes"], s["blocks"]) for s in d["sessions"]] for d in plan["days"]]
    assert prescribed(guarded) == prescribed(original)
