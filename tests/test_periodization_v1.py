"""Behavioral boundaries for the coach-defined periodization calendar."""

from copy import deepcopy
from datetime import date, timedelta

import pytest

from biathlon.periodization import PREPARATION_KINDS, build_periodization


START = date(2026, 1, 1)


def event(day, kind="MAIN_RACE", *, last=None, identifier="race-0001"):
    return {
        "event_id": identifier,
        "event_type": kind,
        "name": "Основен старт" if kind == "MAIN_RACE" else kind,
        "start_date": day.isoformat(),
        "end_date": (last or day).isoformat(),
    }


def plan(preparation_days, **kwargs):
    race_day = START + timedelta(days=preparation_days)
    return build_periodization(START, race_day, [event(race_day)], **kwargs)


def lengths(result):
    return tuple(sum(phase["days"] for phase in result["phases"] if phase["kind"] == kind)
                 for kind in PREPARATION_KINDS)


def assert_contiguous(result):
    phases = result["phases"]
    cursor = date.fromisoformat(result["parameters"]["start_date"])
    for phase in phases:
        assert date.fromisoformat(phase["start_date"]) == cursor
        last = date.fromisoformat(phase["end_date"])
        assert phase["days"] == (last - cursor).days + 1 > 0
        cursor = last + timedelta(days=1)
    assert cursor == date.fromisoformat(result["parameters"]["end_date"]) + timedelta(days=1)
    assert sum(phase["days"] for phase in phases) == result["parameters"]["horizon_days"]


@pytest.mark.parametrize("days,expected", [
    (28, (0, 0, 14, 14)),
    (56, (0, 14, 28, 14)),
    (77, (7, 28, 28, 14)),
    (175, (14, 63, 63, 35)),
    (273, (21, 98, 98, 56)),
])
def test_agreed_calendar_examples(days, expected):
    result = plan(days)
    assert lengths(result) == expected
    assert result["phases"][-1]["kind"] == "COMPETITION"
    assert result["phases"][-1]["days"] == 1
    assert result["phases"][-1]["start_date"] == (START + timedelta(days=days)).isoformat()
    assert_contiguous(result)


def test_extension_is_monotone_capped_and_equal_for_general_and_special():
    previous = (0, 0, 0, 0)
    for days in range(366):
        result = plan(days)
        current = lengths(result)
        assert sum(current) == days
        assert all(after >= before for before, after in zip(previous, current))
        assert current[0] <= 21
        assert current[3] <= 56
        if days >= 77:
            assert abs(current[1] - current[2]) <= 1
        if days > 273:
            assert current[0] == 21 and current[3] == 56
        assert_contiguous(result)
        previous = current


def test_four_week_dates_and_taper_overlay_are_not_double_counted():
    result = plan(28)
    assert [(row["kind"], row["start_date"], row["end_date"])
            for row in result["phases"]] == [
        ("SPECIAL_PREPARATION", "2026-01-01", "2026-01-14"),
        ("PRECOMPETITION", "2026-01-15", "2026-01-28"),
        ("COMPETITION", "2026-01-29", "2026-01-29"),
    ]
    taper = result["taper_windows"][0]
    assert (taper["start_date"], taper["end_date"], taper["days"]) == ("2026-01-22", "2026-01-28", 7)
    assert taper["prevent_deficit_refill"] is True
    assert sum(row["days"] for row in result["phases"]) == 29
    assert any(row["code"] == "PARTIAL_PREPARATION_CYCLE" for row in result["warnings"])


def test_long_precompetition_is_not_an_eight_week_taper():
    result = plan(273)
    assert lengths(result)[3] == 56
    assert result["taper_windows"][0]["days"] == 7


def test_explicit_entry_reserves_days_and_zero_suppresses_entry():
    assert lengths(plan(175, reentry_days_override=14)) == (14, 63, 63, 35)
    no_entry = plan(175, reentry_days_override=0)
    assert lengths(no_entry)[0] == 0
    assert abs(lengths(no_entry)[1] - lengths(no_entry)[2]) <= 1
    assert_contiguous(no_entry)
    returning = plan(28, reentry_days_override=7)
    assert lengths(returning) == (7, 0, 7, 14)
    assert_contiguous(returning)
    too_short = plan(3, reentry_days_override=7)
    assert lengths(too_short) == (3, 0, 0, 0)
    assert too_short["taper_windows"] == []
    assert any(row["code"] == "REENTRY_TRUNCATED_BY_RACE" for row in too_short["warnings"])


def test_no_target_and_context_do_not_invent_tapers_or_load():
    inputs = [event(START + timedelta(days=4), kind, identifier=kind)
              for kind in ("CONTROL_RACE", "CAMP", "TEST", "UNAVAILABLE")]
    before = deepcopy(inputs)
    result = build_periodization(START, START + timedelta(days=27), inputs)
    assert inputs == before
    assert result["next_main_race"] is None
    assert lengths(result) == (0, 28, 0, 0)
    assert result["taper_windows"] == []
    assert {row["event_type"] for row in result["calendar_context"]} == {row["event_type"] for row in inputs}
    assert "TRANSITION" in result["parameters"]["phase_labels_bg"]
    assert result["parameters"]["transition_policy"] == "COACH_DECISION_REQUIRED"
    assert_contiguous(result)


def test_short_display_window_uses_target_beyond_display_without_changing_phase():
    target = START + timedelta(days=77)
    result = build_periodization(START, START + timedelta(days=6), [event(target)])
    assert lengths(result) == (7, 0, 0, 0)
    assert result["next_main_race"]["start_date"] == target.isoformat()
    assert result["taper_windows"] == []
    assert_contiguous(result)


def test_multiple_main_races_suppress_reentry_in_later_cycle():
    first = START + timedelta(days=77)
    second = first + timedelta(days=85)
    result = build_periodization(START, second, [event(second, identifier="second"), event(first)])
    later = [row for row in result["phases"] if row["main_race_event_id"] == "second"]
    assert all(row["kind"] != "RE_ENTRY" for row in later)
    assert len(result["taper_windows"]) == 2
    assert result["next_main_race"]["event_id"] == "race-0001"
    assert_contiguous(result)


def test_dense_main_races_remain_competition_without_stacked_taper():
    first = START + timedelta(days=28)
    second = first + timedelta(days=7)
    result = build_periodization(START, second, [event(first), event(second, identifier="second")])
    assert len(result["taper_windows"]) == 1
    assert all(row["kind"] == "COMPETITION" for row in result["phases"]
               if row["start_date"] >= first.isoformat())
    assert any(row["code"] == "DENSE_MAIN_RACES_REQUIRE_REVIEW" for row in result["warnings"])
    assert_contiguous(result)


def test_race_ranges_and_overlap_cover_each_day_once():
    first = START - timedelta(days=2)
    result = build_periodization(START, START + timedelta(days=10), [
        event(first, last=START + timedelta(days=2)),
        event(START + timedelta(days=1), last=START + timedelta(days=4), identifier="second"),
    ])
    assert sum(row["days"] for row in result["phases"] if row["kind"] == "COMPETITION") == 5
    assert any(row["code"] == "OVERLAPPING_MAIN_RACES" for row in result["warnings"])
    assert any(row["code"] == "POST_RACE_PERIOD_REQUIRES_REVIEW" for row in result["warnings"])
    assert_contiguous(result)


def test_taper_can_be_disabled_or_clipped_to_remaining_preparation():
    assert plan(28, taper_days=0)["taper_windows"] == []
    short = plan(3)
    assert short["taper_windows"][0]["days"] == 3
    assert any(row["code"] == "TAPER_TRUNCATED_BY_AVAILABLE_PREPARATION" for row in short["warnings"])


def test_target_beyond_year_does_not_stretch_calendar():
    result = build_periodization(START, START + timedelta(days=30), [event(START + timedelta(days=500))])
    assert lengths(result) == (0, 31, 0, 0)
    assert any(row["code"] == "MAIN_RACE_BEYOND_ANNUAL_WINDOW" for row in result["warnings"])
    assert_contiguous(result)


def test_leap_year_one_year_horizon_is_valid():
    result = build_periodization(date(2028, 1, 1), date(2028, 12, 31), [])
    assert result["parameters"]["horizon_days"] == 366
    assert_contiguous(result)


@pytest.mark.parametrize("kwargs", [
    {"reentry_days_override": -1}, {"reentry_days_override": 22},
    {"reentry_days_override": 1.5}, {"reentry_days_override": True},
    {"taper_days": -1}, {"taper_days": 22}, {"taper_days": 1.5}, {"taper_days": False},
])
def test_invalid_settings_are_rejected(kwargs):
    with pytest.raises(ValueError):
        plan(28, **kwargs)


def test_invalid_horizons_and_events_are_rejected():
    with pytest.raises(ValueError):
        build_periodization(START, START - timedelta(days=1), [])
    with pytest.raises(ValueError):
        build_periodization(START, START + timedelta(days=366), [])
    with pytest.raises(ValueError):
        build_periodization(START, START, [event(START, "TRANSITION")])
    with pytest.raises(ValueError):
        build_periodization(START, START, [event(START, last=START - timedelta(days=1))])
