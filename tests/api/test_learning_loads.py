from datetime import date, timedelta

import pytest

from apps.api import learning_loads as loads, recovery_policy as policy
from biathlon.constants import COMPONENTS
from tests.api.test_recovery_policy import fixture


def sample(values):
    start = date(2026, 10, 1)
    return {(start+timedelta(days=i)).isoformat(): {
        "q": {z: v if z == "Z1" else 0. for z in COMPONENTS},
        "minutes": {z: v if z == "Z1" else 0. for z in COMPONENTS},
        "effective": {z: v if z == "Z1" else 0. for z in COMPONENTS},
        "status": "REST" if v == 0 else "COMPLETE", "precision": 1.,
    } for i, v in enumerate(values)}


def test_three_calendar_days_include_rest_preserve_peak_and_ignore_future():
    daily = sample([100., 0., 50., 900.])
    assert loads.rolling(daily, date(2026, 10, 3), "Z1") == (50., 3)
    assert daily["2026-10-01"]["q"]["Z1"] == 100.
    assert loads.rolling(daily, date(2026, 10, 4), "Z1") == pytest.approx((950/3, 3))


def test_missing_day_does_not_turn_into_rest_or_extend_calendar_window():
    daily = sample([100., 0., 50.])
    del daily["2026-10-02"]
    assert loads.rolling(daily, date(2026, 10, 3), "Z1") == (75., 2)
    assert loads.rolling(daily, date(2026, 10, 4), "Z1") == (50., 1)
    assert loads.rolling(daily, date(2026, 10, 6), "Z1") == (None, 0)


def test_partial_initial_mapping_and_large_changes_keep_observations():
    data = fixture()
    activity = data["source"]["activities"][30]
    activity.update(quality_status="limited", planning_estimated=True,
                    load_source={"initial_heuristic": True})
    activity["zones"][0]["equivalent_time_min"] = 5000.
    daily = loads.ledger(data["source"], data["rows"])
    day = daily[activity["date"]]
    assert day["q"]["Z1"] == 5000.
    assert day["q"]["Z2"] == 20.
    assert day["precision"] < 1
    assert policy.build_view_data(**data)["recovery_policy"]["observations"]["excluded_days"] == 0


def test_unknown_component_or_duplicate_does_not_erase_other_days():
    data = fixture()
    activity = data["source"]["activities"][30]
    activity["zones"][0]["equivalent_time_min"] = float("nan")
    daily = loads.ledger(data["source"], data["rows"])
    assert daily[activity["date"]]["q"]["Z1"] is None
    assert daily[activity["date"]]["q"]["Z2"] == 20.
    assert daily[data["source"]["activities"][31]["date"]]["q"]["Z1"] == 20.
    duplicate = next(r for r in data["rows"] if r["date"] == activity["date"] and r["zone"] == "Z2")
    data["rows"].append(dict(duplicate))
    assert loads.ledger(data["source"], data["rows"])[activity["date"]]["q"]["Z2"] is None


def test_unknown_exclusion_retains_recording_but_cannot_confirm_empty_rest_day():
    data = fixture()
    data["source"]["quality"]["excluded_activities"] = 1
    missing = data["source"]["activities"].pop(0)["date"]
    for row in data["rows"]:
        if row["date"] == missing:
            row["effective_load"] = 0.
    daily = loads.ledger(data["source"], data["rows"])
    assert all(v is None for v in daily[missing]["q"].values())
    assert daily[data["source"]["activities"][0]["date"]]["q"]["Z1"] == 10.


def test_one_result_and_recovered_stress_allow_initial_bounded_shadow_probe():
    from biathlon import individual_learning
    data = fixture()
    evidence = policy.build_view_data(**data)
    models = individual_learning.fit(evidence["episodes"], data["today"])
    report = {"mode": "SHADOW"}
    policy.apply_decision(report, evidence, models, config={"mode": "SHADOW", "max_volume_step_percent": 20},
        allowed_components=["Z1"], phase="GENERAL_PREPARATION", taper=False, today=data["today"])
    assert 0 < report["decision"]["step"] <= .2
    assert report["recovery_policy"]["decision"]["applied_change_percent"] == 0
    assert report["components"]["Z1"]["volume_factor"] == 1.
    assessments=policy.build_component_assessments(evidence,today=data["today"],models=models,report=report)
    assert assessments[0]["evidence_count"]==1
    assert assessments[0]["predicted_performance_percent"] is not None
    assert assessments[0]["response_status"]=="UNCERTAIN"
    assert assessments[0]["predicted_stress_points"] is not None


def test_no_elevated_stress_does_not_invent_recovery_time_for_initial_probe():
    from biathlon import individual_learning
    data = fixture(elevated=False, performance=None)
    evidence = policy.build_view_data(**data)
    models = individual_learning.fit(evidence["episodes"], data["today"])
    report = {"mode": "SHADOW"}
    policy.apply_decision(report, evidence, models, config={"mode": "SHADOW"},
        allowed_components=["Z1"], phase="GENERAL_PREPARATION", taper=False, today=data["today"])
    assert evidence["episodes"][0]["response"]["recovery_days"] is None
    assert report["decision"]["tolerated_without_elevation"]
    assert report["decision"]["ratio"] is None
    assert 0 < report["decision"]["step"] <= .2
