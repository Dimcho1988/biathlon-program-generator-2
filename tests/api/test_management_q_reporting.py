from copy import deepcopy
from datetime import date

import pytest

from apps.api.management_lifecycle import reconcile
from apps.api.planning_search import preparation_repair_policy
from biathlon.constants import COMPONENTS
from biathlon.planning_q import actual_day, execution_report, prescribed_day

DAY = "2026-10-27"
TODAY = date(2026, 10, 28)


def source():
    return {"daily": [{"date": DAY, "zone": z, "effective_load": 999.} for z in COMPONENTS if z != "STR"],
            "strength": {"daily": [{"date": DAY, "effective_load": 12.}]},
            "activities": [{"date": DAY, "sport": "Run", "duration_min": 90., "activity_ref": "run",
                            "zones": [{"zone": z, "equivalent_time_min": 3.} for z in COMPONENTS if z != "STR"]}]}


def session(q=10.):
    return {"title": "Session", "sport": "Run", "total_minutes": 120.,
            "direct_equivalent_minutes": {z: q for z in COMPONENTS},
            "canonical_effective_load": {z: 999. for z in COMPONENTS}}


def test_actual_q_uses_direct_activity_ledger_not_clock_or_aerobic_e():
    data = source()
    data["activities"].append(deepcopy(data["activities"][0]))
    data["activities"].append({"date": DAY, "sport": "WeightTraining", "duration_min": 60.})
    assert actual_day(data, DAY) == {z: 12. if z == "STR" else 6. for z in COMPONENTS}
    assert actual_day(data, "2026-10-26") == {z: None for z in COMPONENTS}
    data["activities"] = []
    assert actual_day(data, DAY)["Z3"] == 0.


@pytest.mark.parametrize("bad", [None, -1., float("nan"), float("inf"), True])
def test_unknown_direct_activity_component_never_becomes_zero(bad):
    data = source()
    data["activities"][0]["zones"][2]["equivalent_time_min"] = bad
    assert actual_day(data, DAY)["Z3"] is None
    assert actual_day(data, DAY)["Z2"] == 3.


def test_multiple_prescriptions_and_live_actual_keep_their_own_units():
    plan = {"days": [{"date": DAY, "sessions": [session(10), session(20)], "status": "PLANNED"}]}
    saved = deepcopy(plan)
    first = reconcile(plan, source(), TODAY, {})
    assert first[0]["planned_q"]["Z3"] == 30.
    assert first[0]["actual_q"]["Z3"] == 3.
    assert first[0]["planned_minutes"] == 240.
    changed = source()
    changed["activities"][0]["zones"][2]["equivalent_time_min"] = 4.
    next_plan = {"days": [{"date": DAY, "sessions": [], "status": "EXISTING_ACTIVITY"}]}
    second = reconcile(next_plan, changed, TODAY, {}, first)
    assert second[0]["planned_q"]["Z3"] == 30.
    assert second[0]["actual_q"]["Z3"] == 4.
    assert plan == saved


def test_legacy_missing_prescription_q_is_unknown_and_never_rebuilt_from_e():
    prior = [{"date": DAY, "planned_title": "Legacy", "planned_minutes": 20., "planned_sport": "Run",
              "planned_load": {z: 500. for z in COMPONENTS}}]
    row = reconcile({"days": []}, source(), TODAY, {}, prior)[0]
    assert all(v is None for v in row["planned_q"].values())
    assert prescribed_day({"status": "EXISTING_ACTIVITY"})["Z3"] is None


def test_report_covers_whole_goal_windows_excludes_future_actuals_and_keeps_unknown_days():
    plan = {"allocation": {"segments": [{"window_start": "2026-10-26", "window_end": "2026-11-01"}]}}
    report = execution_report(source(), TODAY, [plan])
    assert [d["date"] for d in report["days"]] == ["2026-10-26", DAY, "2026-10-28"]
    assert report["days"][0]["q"]["Z3"] is None
    assert report["days"][1]["q"]["Z3"] == 3.
    assert report["days"][2]["day_complete"] is False
    retained = execution_report({}, TODAY, [plan], report)
    assert retained["days"][1]["q"]["Z3"] == 3.
    corrected = source()
    corrected["activities"][0]["zones"] = []
    assert execution_report(corrected, TODAY, [plan], report)["days"][1]["q"]["Z3"] is None


def repair_plan():
    rows = {z: {"basis": "DIRECT_Q", "target": 100., "actual": 20., "planned": 80.} for z in COMPONENTS}
    rows["Z4"]["planned"] = 60.
    return {"allocation": {"segments": [{"window_start": "2026-11-03", "window_end": "2026-11-09", "components": rows}]},
            "parameters": {"allocation_policy": {"component_gap_first": True}},
            "days": [{"date": "2026-11-04", "sessions": [{"zone": "Z3", "main_work_minutes": 70.,
                     "dose_evidence": {"minimum_structure_work_minutes": 14.},
                     "direct_equivalent_minutes": {"Z1": 50., "Z3": 20.}}]}],
            "lookahead_days": [{"date": "2026-11-09", "sessions": [{"zone": "Z4"}]}]}


def test_preparation_repair_uses_candidate_dates_and_whole_minimum_without_mutating_it():
    plan = repair_plan()
    original = deepcopy(plan)
    assert preparation_repair_policy(plan) == {"component_gap_first": True,
        "easy_caps": {"2026-11-04": 14.}, "easy_cap_zones": ["Z3"]}
    assert plan == original
    plan["days"][0]["locked"] = True
    assert preparation_repair_policy(plan) is None


def test_repair_cannot_borrow_from_another_cycle_or_an_unknown_or_short_component():
    for change in ("cycle", "unknown", "short"):
        plan = repair_plan()
        if change == "cycle": plan["lookahead_days"][0]["date"] = "2026-11-10"
        if change == "unknown": plan["allocation"]["segments"][0]["components"]["Z3"]["actual"] = None
        if change == "short": plan["allocation"]["segments"][0]["components"]["Z3"]["planned"] = 60.
        assert preparation_repair_policy(plan) is None


def test_estimated_part_is_explicit_and_matches_the_planning_q_ledger():
    data = source()
    activity = data['activities'][0]
    activity['planning_estimated'] = True
    for row in activity['zones']:
        row['planning_estimated_q'] = 1.
        row['equivalent_time_min'] = 4.
    plan = {'start_date': DAY, 'end_date': '2026-11-02'}
    report = execution_report(data, TODAY, [plan])
    row = report['days'][0]
    assert row['q']['Z3'] == 4.
    assert row['estimated_q']['Z3'] == 1.
    assert row['estimated_activity_count'] == 1
    outcomes = reconcile({'days': [{'date': DAY, 'sessions': [session()]}]}, source(), TODAY, {}, q_execution=report)
    assert outcomes[0]['actual_q']['Z3'] == 4.
    assert outcomes[0]['actual_q_estimated']['Z3'] == 1.
