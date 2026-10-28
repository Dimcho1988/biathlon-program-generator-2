from datetime import date, timedelta

import pytest

from apps.api.initial_threshold_view import build


TODAY = date(2026, 10, 28)


def calendar(days=28):
    dates = [(TODAY-timedelta(days=i)).isoformat() for i in range(1, days+1)]
    return {"activities": [{"activity_ref": "run1", "sport": "Run", "local_date": dates[0],
              "recording_time_s": 3600, "moving_time_s": 3000, "distance_m": 10000,
              "quality_status": "limited", "hr_coverage_percent": 0},
             {"activity_ref": "str1", "sport": "WeightTraining", "local_date": dates[0],
              "recording_time_s": 1800}],
            "snapshot_payload": {"load_history": {
                "period_start": dates[-1], "period_end": dates[0],
                "quality": {"processed_activities": 2, "limited_activities": 1,
                            "excluded_activities": 0, "no_activity_days": days-1, "warnings": []},
                "daily": [{"date": day, "zone": zone} for day in dates for zone in ("Z1", "Z2", "Z3", "Z4", "Z5")],
                "strength": {"daily": [{"date": day} for day in dates]}}}}


def run(value=None, **kwargs):
    return build(value if value is not None else calendar(), {"training_experience_years": 4}, TODAY, "Run", **kwargs)


def test_raw_catalog_speed_and_all_sport_hours_without_hr_or_extra_reads():
    result = run()
    assert result["use_status"] == "THRESHOLD_ONLY"
    assert result["inputs"] == {"H": .375, "v": 12., "S": 4.}
    assert result["window"]["days"] == 28
    assert result["used_for_zones"] is False
    assert result["speed_role"] == "RAW_MOVING_SPEED"


@pytest.mark.parametrize("days", [7, 21, 27])
def test_shorter_reliable_history_keeps_the_actual_calendar_denominator(days):
    result = run(calendar(days))
    assert result["window"]["days"] == days
    assert result["inputs"]["H"] == pytest.approx(10.5/days)


def test_requested_21_day_window_and_missing_coverage():
    assert run(requested_days=21)["window"]["days"] == 21
    assert run(calendar(20), requested_days=21)["reason"] == "INSUFFICIENT_HISTORY"
    assert run(calendar(6))["reason"] == "INSUFFICIENT_HISTORY"
    value = calendar()
    value["snapshot_payload"]["load_history"]["daily"].pop(0)
    assert run(value)["reason"] == "INSUFFICIENT_HISTORY"


def test_missing_strength_buckets_do_not_discard_complete_recorded_catalog():
    value = calendar()
    value["snapshot_payload"]["load_history"].pop("strength")
    assert run(value)["inputs"]["H"] == .375


def test_simpler_heuristic_never_supersedes_reliable_anchors():
    for anchor in ("TESTS", "INDEX"):
        result = build({}, None, TODAY, "Run", reliable_anchor=anchor)
        assert result["use_status"] == "SUPERSEDED"
        assert result["superseded_by"] == anchor
        assert result["v_threshold_estimated_kmh"] is None


def test_missing_staj_stays_missing_and_explicit_zero_is_valid():
    assert build(calendar(), {}, TODAY, "Run")["reason"] == "TRAINING_YEARS_REQUIRED"
    assert build(calendar(), {"training_experience_years": 0}, TODAY, "Run")["status"] == "AVAILABLE"


def test_missing_moving_time_does_not_use_recording_elapsed_or_average_speed():
    value = calendar()
    value["activities"][0].update(moving_time_s=None, elapsed_time_s=3600, average_speed_mps=4)
    assert run(value)["reason"] == "MOVING_TIME_REQUIRED"


def test_matching_canonical_duration_precedes_recording_time():
    value = calendar()
    value["activities"][0]["canonical_summary"] = {"duration_min": 70}
    result = run(value)
    assert result["inputs"]["H"] == pytest.approx(7*100/60/28)


def test_plans_are_excluded_and_duplicates_do_not_inflate_estimate():
    value = calendar()
    row = value["activities"][0]
    value["activities"].extend([dict(row), dict(row, activity_ref="plan", planned=True)])
    assert run(value)["inputs"] == run()["inputs"]


def test_provider_missing_is_distinct_from_missing_hr():
    value = calendar()
    value["activities"][0]["quality_status"] = "provider_missing"
    value["snapshot_payload"]["load_history"]["quality"].update(limited_activities=0, excluded_activities=1)
    assert run(value)["reason"] == "INSUFFICIENT_HISTORY"


def test_real_provider_failure_cannot_hide_behind_complete_daily_buckets():
    # A missing provider detail increments these real persisted counters but
    # has no catalog metadata; there is no provider_missing_activities field.
    value = calendar()
    value["snapshot_payload"]["load_history"]["quality"].update(processed_activities=3, excluded_activities=1)
    result = run(value)
    assert result["reason"] == "INCOMPLETE_HISTORY_COVERAGE"
    assert result["v_threshold_estimated_kmh"] is None


def test_identifiable_excluded_no_hr_record_keeps_raw_training_evidence():
    value = calendar()
    value["activities"][0].update(quality_status="excluded", quality_reason="No usable HR", hr_coverage_percent=0)
    value["snapshot_payload"]["load_history"]["quality"].update(limited_activities=0, excluded_activities=1)
    assert run(value)["inputs"] == {"H": .375, "v": 12., "S": 4.}


def test_count_reconciliation_uses_source_period_and_unique_completed_ids():
    value = calendar()
    row = value["activities"][0]
    value["activities"].extend([dict(row), dict(row, activity_ref="old", local_date="2026-09-01"),
                                dict(row, activity_ref="plan", planned=True)])
    assert run(value)["status"] == "AVAILABLE"
    value["snapshot_payload"]["load_history"]["quality"]["processed_activities"] = 3
    assert run(value)["reason"] == "INCOMPLETE_HISTORY_COVERAGE"


def test_matching_total_count_cannot_conceal_unidentified_excluded_record():
    value = calendar()
    value["snapshot_payload"]["load_history"]["quality"]["excluded_activities"] = 1
    assert run(value)["reason"] == "INCOMPLETE_HISTORY_COVERAGE"


@pytest.mark.parametrize("field,value", [("period_start", None), ("period_end", "invalid")])
def test_global_counts_without_valid_source_dates_cannot_prove_coverage(field, value):
    source = calendar()
    source["snapshot_payload"]["load_history"][field] = value
    assert run(source)["reason"] == "INCOMPLETE_HISTORY_COVERAGE"
