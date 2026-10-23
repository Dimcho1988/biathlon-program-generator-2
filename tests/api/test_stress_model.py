from copy import deepcopy
from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from apps.api.response_monitoring import DailyReport, SessionReport, build_history, latest_entries
from apps.api.stress_model import CHANNELS, freeze_baseline, laboratory_observation, test_observation as observe_test
from scripts.generate_response_fixture import fixture
from tests.api.test_response_monitoring import TODAY, inputs, entry, report, activity
from tests.api.test_body_observations import weight, lab


def channel(day, name):
    return next(c for c in day["channels"] if c["key"] == name)


def test_full_model_and_cross_language_fixture_have_exact_weights_and_contributions():
    h = fixture()
    d = h["days"][-1]
    assert sum(weight for _, weight in CHANNELS.values()) == 100
    assert d["coverage"] == 100 and d["assessment_quality"] == "SUFFICIENT"
    assert len(d["channels"]) == 22
    for g in d["groups"]:
        assert sum(c["weight"] for c in d["channels"] if c["group"] == g["key"]) == pytest.approx(g["weight"])
    assert sum(c["contribution"] for c in d["channels"]) == pytest.approx(d["total"], abs=.01)
    assert sum(g["contribution"] for g in d["groups"]) == pytest.approx(d["total"], abs=.003)
    assert h["days"][7]["assessment_quality"] == "PARTIAL"
    assert h["days"][7]["trend_3d"] is None
    assert h["days"][8]["mix_changed"]


def test_partial_questionnaire_is_not_filled_with_normal_answers():
    body = DailyReport(day=TODAY, fatigue=5)
    h = build_history(entries=[entry("DAILY", TODAY, body.model_dump(mode="json"))], wellness=[], activities=[], start=TODAY, end=TODAY, today=TODAY)
    d = h["days"][0]
    assert d["total"] == 100 and d["coverage"] == 8 and d["assessment_quality"] == "PARTIAL"
    assert channel(d, "motivation")["score"] is None
    with pytest.raises(ValidationError):
        DailyReport(day=TODAY)


def test_no_data_is_null_not_zero_and_subjective_provider_scales_are_not_guessed():
    h = build_history(entries=[], wellness=[{"date": str(TODAY), "metrics": {"fatigue": {"value": 5, "unit": "score"}}}], activities=[], start=TODAY, end=TODAY, today=TODAY)
    assert h["days"][0]["total"] is None and h["days"][0]["coverage"] == 0


def test_sleep_uses_prior_days_and_manual_value_overrides_seconds_import():
    data = inputs()
    for row in data["wellness"]:
        row["metrics"]["sleep_duration"] = {"value": 8*3600, "unit": "s"}
    data["entries"][-5]["payload"]["sleep_hours"] = 6  # current DAILY before the four sessions
    d = build_history(**data)["days"][0]
    assert channel(d, "sleep_duration")["raw"] == 6
    assert channel(d, "sleep_duration")["baseline"]["median"] == 8
    assert channel(d, "sleep_duration")["score"] == 100


@pytest.mark.parametrize("reason,counts", [("FATIGUE", True), ("AS_PLANNED", True), ("TIME", False), ("CONDITIONS", False), ("COACH", False), ("UNKNOWN", False)])
def test_execution_only_scores_comparable_physical_response(reason, counts):
    a = activity(TODAY)
    p = SessionReport(activity_ref=a["activity_ref"], duration_minutes=45, planned_duration_minutes=60,
                      planned_speed_kmh=15, executed_speed_kmh=13, execution_comparable=True, execution_reason=reason)
    h = build_history(entries=[entry("SESSION", a["activity_ref"], p.model_dump(mode="json"))], wellness=[], activities=[a], start=TODAY, end=TODAY, today=TODAY)
    d = h["days"][0]
    assert (channel(d, "performance")["score"] is not None) == counts
    assert (channel(d, "volume")["score"] is not None) == counts
    assert channel(d, "rpe")["score"] is None


def test_weight_drift_is_detected_against_frozen_block_reference():
    old = [weight(TODAY-timedelta(days=i), 70) for i in range(8, 29)]
    anchor = TODAY-timedelta(days=7)
    fixed = freeze_baseline(latest_entries(old), {}, anchor)
    rows = old+[weight(TODAY-timedelta(days=i), 68) for i in range(7)]
    rows.append(entry("BLOCK", anchor, {"start": str(anchor), "load_end": str(TODAY-timedelta(days=2)), "recovery_end": str(TODAY),
                                      "phase": "BUILD", "baseline_frozen_on": str(anchor), "stress_baseline": fixed}))
    h = build_history(entries=rows, wellness=[], activities=[], start=TODAY, end=TODAY, today=TODAY)
    d = h["days"][0]
    assert d["body_observations"]["weight"]["morning_ratio"] == 1
    assert channel(d, "morning_weight")["score"] == 100
    assert channel(d, "morning_weight")["baseline"]["median"] == 70


def test_real_workbook_formula_direction_matches_ratio_of_paired_means():
    e = weight(TODAY, 69, sessions=[{"session": 1, "before_kg": 69.6, "after_kg": 68.3, "comparable": True},
                                    {"session": 2, "before_kg": 69.5, "after_kg": 68.5, "comparable": True}])
    h = build_history(entries=[e], wellness=[], activities=[], start=TODAY, end=TODAY, today=TODAY)
    w = h["days"][0]["body_observations"]["weight"]
    assert w["session_ratio"] == pytest.approx(68.4/69.55)


def test_lab_requires_three_prior_sampling_days_and_cannot_be_carried_to_tomorrow():
    rows = [lab(TODAY-timedelta(days=i), f"{i:032x}") for i in (7, 14, 21)]
    rows.append(lab(TODAY, "f"*32, results=[dict(analyte="CK", value=500, unit="U/L", sample="SERUM", reference_high=180)]))
    h = build_history(entries=rows, wellness=[], activities=[], start=TODAY, end=TODAY+timedelta(days=1), today=TODAY+timedelta(days=1))
    d, tomorrow = h["days"]
    assert channel(d, "CK")["score"] == 100 and d["state"] == "REVIEW"
    assert channel(tomorrow, "CK")["score"] is None and tomorrow["total"] is None
    assert h["lab_reports"][-1]["age_days"] == 1
    # Neither future samples nor repeated same-day results create a baseline.
    assert laboratory_observation(h["lab_reports"], str(TODAY-timedelta(days=14)), "CK") is None


def test_tc_ratio_has_one_vote_and_does_not_mix_different_samples():
    h = fixture()
    d = h["days"][-1]
    assert channel(d, "TC_RATIO")["score"] is not None
    assert not any(c["key"] in ("TESTOSTERONE", "CORTISOL") for c in d["channels"])
    for r in h["lab_reports"]:
        r["testosterone_cortisol_ratio"] = None
    assert laboratory_observation(h["lab_reports"], d["day"], "TC_RATIO") is None


def test_control_tests_compare_same_protocol_and_direction_only():
    tests = [{"day": str(TODAY-timedelta(days=i)), "protocol": "run", "protocol_version": "1", "unit": "s", "direction": "LOWER", "conditions": "track", "comparable": True, "value": 600, "meaningful_change_percent": 1} for i in (0, 7, 14, 21)]
    tests[0]["value"] = 630
    assert observe_test(tests, str(TODAY))["score"] == 100
    tests[-1]["conditions"] = "snow"
    assert observe_test(tests, str(TODAY)) is None


def test_short_chart_keeps_same_three_day_trend_and_missing_bad_signal_is_not_recovery():
    data = inputs()
    broad = build_history(**{**data, "start": TODAY-timedelta(days=5)})
    narrow = build_history(**data)
    assert narrow["days"][0]["trend_3d"] == broad["days"][-1]["trend_3d"]
    previous = deepcopy(data)
    for row in previous["wellness"]:
        if row["date"] == str(TODAY-timedelta(days=1)):
            row["metrics"]["resting_hr"]["value"] = 75
    previous["wellness"][-1]["metrics"].pop("resting_hr")
    d = build_history(**previous)["days"][0]
    assert d["mix_changed"] and d["comparison_previous"]["delta"] is None
    assert d["trend_3d"] is None


def test_future_observations_do_not_change_current_estimate():
    data = inputs()
    before = build_history(**data)
    data["entries"].append(weight(TODAY+timedelta(days=1), 100))
    data["entries"].append(entry("DAILY", TODAY+timedelta(days=1), report(TODAY+timedelta(days=1), 5)))
    assert build_history(**data)["days"] == before["days"]
