from datetime import date, timedelta
import math

import pytest

from biathlon import initial_threshold as model


def activity(identity="run", *, sport="Run", day="2026-10-20", duration=3600., moving=3000., distance=10000., **extra):
    return {"id": identity, "sport": sport, "local_date": day, "completed": True,
            "training_duration_s": duration, "moving_time_s": moving, "distance_m": distance, **extra}


def history(rows, days=7, **changes):
    start, end = date(2026, 10, 28)-timedelta(days=days), date(2026, 10, 28)
    args = {"sport": "Run", "training_years": 4, "window_start": start,
            "window_end": end, "as_of_date": end,
            "reliable_dates": [start+timedelta(days=i) for i in range(days)]}
    args.update(changes)
    return model.from_history(rows, **args)


def test_approved_control_example_and_estimate_provenance():
    result = model.estimate(8, 12, 4)
    assert result["p_percent"] == pytest.approx(80.5476, abs=.00005)
    assert result["v_threshold_estimated_kmh"] == pytest.approx(14.8980, abs=.00005)
    assert result["model_version"] == "heuristic_v0.1_unvalidated"
    assert result["speed_role"] == "RAW_MOVING_SPEED"
    assert result["is_estimated"] and result["calibration_eligible"] is False
    assert not any(key in result for key in ("v6_kmh", "v50_kmh", "zones"))


@pytest.mark.parametrize("field,value", [(i, v) for i in range(3) for v in (None, True, "4", -1, math.inf, math.nan)] + [(0, 0), (1, 0)])
def test_invalid_inputs_do_not_produce_a_threshold(field, value):
    values = [8, 12, 4]
    values[field] = value
    result = model.estimate(*values)
    assert result["status"] == "UNAVAILABLE"
    assert result["p_percent"] is None and result["v_threshold_estimated_kmh"] is None


def test_zero_training_years_is_valid_but_missing_is_not_assumed_zero():
    assert model.estimate(8, 12, 0)["status"] == "AVAILABLE"
    assert history([activity(day="2026-10-22")], training_years=None)["reason"] == "TRAINING_YEARS_REQUIRED"


@pytest.mark.parametrize("days", [7, 21, 28])
def test_calendar_denominator_includes_rest_days_and_all_sports(days):
    run = activity(day="2026-10-22")
    strength = activity("strength", sport="Strength", day="2026-10-23", duration=1800, moving=None, distance=None)
    result = history([run, strength], days=days)
    assert result["status"] == "AVAILABLE"
    assert result["inputs"]["H"] == pytest.approx(7 * 1.5 / days)
    assert result["inputs"]["v"] == pytest.approx(12.)
    assert result["window"]["days"] == days


def test_speed_is_distance_over_matching_moving_time_not_average_session_speed():
    # 10 km / 50 min and 20 km / 2 h: combined 10.588..., not (12+10)/2.
    result = history([activity(day="2026-10-22"), activity("long", day="2026-10-23", duration=9000, moving=7200, distance=20000)])
    assert result["inputs"]["v"] == pytest.approx(30000 / 10200 * 3.6)
    assert result["inputs"]["H"] == pytest.approx(3.5)
    assert result["history"]["sport_moving_time_s"] == 10200


def test_identical_duplicate_does_not_inflate_volume_or_distance():
    row = activity(day="2026-10-22")
    result = history([row, dict(row, irrelevant_metadata="different")])
    assert result["inputs"]["H"] == 1
    assert result["history"]["duplicates_removed"] == 1
    assert result["history"]["sport_distance_m"] == 10000


def test_conflicting_duplicate_is_not_arbitrarily_selected():
    row = activity(day="2026-10-22")
    assert history([row, dict(row, distance_m=20000)])["reason"] == "CONFLICTING_DUPLICATE_ACTIVITY"


def test_planned_unfinished_current_and_outside_window_rows_are_excluded():
    rows = [activity(day="2026-10-22"), activity("old"),
            activity("today", day="2026-10-28"),
            activity("planned", day="2026-10-23", planned=True),
            activity("unfinished", day="2026-10-23", completed=False)]
    result = history(rows)
    assert result["history"]["activity_count"] == 1


@pytest.mark.parametrize("patch,reason", [
    ({"moving_time_s": None}, "MOVING_TIME_REQUIRED"),
    ({"distance_m": None}, "DISTANCE_REQUIRED"),
    ({"training_duration_s": None}, "ACTIVITY_DURATION_REQUIRED"),
    ({"moving_time_s": 4000}, "MOVING_TIME_EXCEEDS_DURATION"),
    ({"id": None}, "ACTIVITY_ID_REQUIRED"),
    ({"completed": None}, "ACTIVITY_COMPLETION_REQUIRED"),
])
def test_missing_inputs_cannot_silently_change_the_denominator(patch, reason):
    assert history([dict(activity(day="2026-10-22"), **patch)])["reason"] == reason


def test_missing_strength_duration_blocks_total_volume_even_with_valid_run():
    result = history([activity(day="2026-10-22"), activity("s", sport="Strength", day="2026-10-23", duration=None)])
    assert result["reason"] == "ACTIVITY_DURATION_REQUIRED"


def test_minimum_history_and_complete_calendar_coverage_are_required():
    assert history([], days=6)["reason"] == "INSUFFICIENT_HISTORY"
    assert history([], reliable_dates=[date(2026, 10, 22)])["reason"] == "INCOMPLETE_HISTORY_COVERAGE"
    assert history([], reliable_dates=None)["reason"] == "INCOMPLETE_HISTORY_COVERAGE"
    assert history([], window_end="2026-10-29")["reason"] == "INCOMPLETE_CALENDAR_DAYS"
    assert history([], window_start="invalid")["reason"] == "INVALID_WINDOW"
    assert history([])["reason"] == "SPORT_HISTORY_REQUIRED"


def test_only_exact_sport_is_used_for_speed_but_other_sports_add_volume():
    result = history([activity(day="2026-10-22"), activity("trail", sport="TrailRun", day="2026-10-23", duration=7200, distance=5000, moving=7200)])
    assert result["inputs"]["H"] == 3
    assert result["inputs"]["v"] == 12
    assert result["history"]["sport_activity_count"] == 1


def test_extreme_inputs_fail_without_nonfinite_outputs_or_exceptions():
    assert model.estimate(10**1000, 12, 4)["status"] == "UNAVAILABLE"
    result = history([activity("a", day="2026-10-22", duration=1e308),
                      activity("b", day="2026-10-23", duration=1e308)])
    assert result["reason"] == "NUMERIC_RANGE_ERROR"


def test_formula_has_no_implicit_clamp_or_added_coefficients():
    result = model.estimate(.1, 100, 0)
    expected = 90 * 1.1**(-.04) * 10**.05
    assert result["p_percent"] == pytest.approx(expected)
    assert result["p_percent"] > 100
