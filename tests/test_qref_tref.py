from __future__ import annotations

from datetime import date, timedelta

import pytest

from intervals_inspector.onflows_intrazone_load import (
    calculate_onflows_intrazone_load,
)
from intervals_inspector.onflows_zone_profile import (
    default_onflows_zone_profile,
)
from intervals_inspector.qref_planning import (
    SECONDARY_DIRECT_RATIO_THRESHOLD,
    adjust_target_for_recovery,
    direct_ratio,
    limiting_secondary_zones,
    session_dose_range,
    weekly_target,
)
from intervals_inspector.shadow_model import (
    calculate_shadow_result,
    configuration_with_overrides,
    configuration_with_profile_level,
    default_shadow_configuration,
)
from intervals_inspector.stream_normalizer import (
    NormalizerInput,
    normalize_stream_intervals,
)


def _constant_hr_analysis(hr: float, *, minutes: int = 60):
    seconds = minutes * 60
    result = normalize_stream_intervals(
        NormalizerInput(
            offsets=list(range(seconds + 1)),
            metrics={"heartrate": [hr] * (seconds + 1)},
            elapsed_time_sec=float(seconds),
            icu_recording_time_sec=float(seconds),
        )
    )
    return calculate_onflows_intrazone_load(
        result, default_onflows_zone_profile()
    )


def _zone(analysis, zone: str):
    return next(row for row in analysis["zones"] if row["zone"] == zone)


def _empty_analysis(*, qref: dict[str, float] | None = None):
    configuration = default_shadow_configuration()
    qref = qref or {}
    return {
        "hr_coverage_percent": 100.0,
        "zones": [
            {
                "zone": zone.zone,
                "real_seconds": 0.0,
                "weighted_seconds": 0.0,
                "qref_seconds": 60.0 * float(qref.get(zone.zone, 0.0)),
            }
            for zone in configuration.zones
        ],
    }


def _history(
    current: date,
    days: int,
    *,
    weekly_qref: float,
    zone: str = "Z2",
):
    daily = weekly_qref / 7.0
    return [
        {
            "date": (current - timedelta(days=days - index)).isoformat(),
            zone: daily,
        }
        for index in range(days)
    ]


@pytest.mark.parametrize(
    ("zone", "hr"),
    (("Z1", 125.0), ("Z2", 145.0), ("Z3", 162.0), ("Z4", 177.0)),
)
def test_01_z1_to_z4_at_upper_boundary_qref_equals_real_time(
    zone: str, hr: float
) -> None:
    row = _zone(_constant_hr_analysis(hr), zone)

    assert row["real_minutes"] == pytest.approx(60.0)
    assert row["qref_minutes"] == pytest.approx(60.0)


@pytest.mark.parametrize(
    ("zone", "hr"),
    (("Z1", 100.0), ("Z2", 126.0), ("Z3", 146.0), ("Z4", 163.0)),
)
def test_02_z1_to_z4_in_lower_part_qref_is_below_real_time(
    zone: str, hr: float
) -> None:
    row = _zone(_constant_hr_analysis(hr), zone)

    assert row["real_minutes"] == pytest.approx(60.0)
    assert row["qref_minutes"] < row["real_minutes"]


def test_03_z5_at_lower_boundary_qref_equals_real_time() -> None:
    row = _zone(_constant_hr_analysis(178.0), "Z5")

    assert row["real_minutes"] == pytest.approx(60.0)
    assert row["qref_minutes"] == pytest.approx(60.0)


def test_04_z5_above_lower_boundary_qref_exceeds_real_time() -> None:
    row = _zone(_constant_hr_analysis(190.0), "Z5")

    assert row["real_minutes"] == pytest.approx(60.0)
    assert row["qref_minutes"] > row["real_minutes"]


def test_05_same_time_and_higher_hr_produces_higher_qref() -> None:
    low = _zone(_constant_hr_analysis(146.0), "Z3")
    high = _zone(_constant_hr_analysis(162.0), "Z3")

    assert high["real_minutes"] == pytest.approx(low["real_minutes"])
    assert high["qref_minutes"] > low["qref_minutes"]


@pytest.mark.parametrize(
    ("historical", "expected"),
    ((70.0, 90.0), (120.0, 120.0), (220.0, 180.0)),
)
def test_06_h40_is_clamped_below_between_and_above_bounds(
    historical: float, expected: float
) -> None:
    current = date(2026, 8, 9)
    result = calculate_shadow_result(
        _empty_analysis(),
        default_shadow_configuration(),
        prior_daily_qref=_history(
            current, 40, weekly_qref=historical
        ),
        activity_date=current,
    )
    row = next(item for item in result["rows"] if item["zone"] == "Z2")

    assert row["tref_history_value"] == pytest.approx(historical)
    assert row["tref_effective"] == pytest.approx(expected)
    assert row["tref_source"] == "40-day history"


@pytest.mark.parametrize(
    ("level", "expected"),
    (("low", 90.0), ("medium", 135.0), ("high", 180.0)),
)
def test_07_profile_low_medium_high_values(level: str, expected: float) -> None:
    configuration = configuration_with_profile_level(level)
    result = calculate_shadow_result(_empty_analysis(), configuration)
    row = next(item for item in result["rows"] if item["zone"] == "Z2")

    assert row["tref_effective"] == pytest.approx(expected)
    assert row["tref_source"] == "profile"


@pytest.mark.parametrize("days", (7, 20, 39))
def test_08_seven_to_thirty_nine_days_use_provisional_history(days: int) -> None:
    current = date(2026, 8, 9)
    result = calculate_shadow_result(
        _empty_analysis(),
        default_shadow_configuration(),
        prior_daily_qref=_history(current, days, weekly_qref=120.0),
        activity_date=current,
    )
    row = next(item for item in result["rows"] if item["zone"] == "Z2")

    assert row["tref_history_value"] == pytest.approx(120.0)
    assert row["tref_effective"] == pytest.approx(120.0)
    assert row["tref_source"] == "provisional history"
    assert row["tref_history_days"] == days


def test_09_rest_days_are_zero_calendar_days_in_provisional_history() -> None:
    current = date(2026, 8, 9)
    history = [
        {
            "date": (current - timedelta(days=10 - index)).isoformat(),
            "Z2": 100.0 if index == 0 else 0.0,
        }
        for index in range(10)
    ]
    result = calculate_shadow_result(
        _empty_analysis(),
        default_shadow_configuration(),
        prior_daily_qref=history,
        activity_date=current,
    )
    row = next(item for item in result["rows"] if item["zone"] == "Z2")

    assert row["tref_history_days"] == 10
    assert row["tref_history_value"] == pytest.approx(70.0)
    assert row["tref_effective"] == pytest.approx(90.0)


def test_10_current_and_future_data_never_change_retrospective_tref() -> None:
    current = date(2026, 8, 9)
    past = _history(current, 40, weekly_qref=120.0)
    with_future = [
        *past,
        {"date": current.isoformat(), "Z2": 10000.0},
        {"date": (current + timedelta(days=1)).isoformat(), "Z2": 10000.0},
    ]
    baseline = calculate_shadow_result(
        _empty_analysis(),
        default_shadow_configuration(),
        prior_daily_qref=past,
        activity_date=current,
    )
    compared = calculate_shadow_result(
        _empty_analysis(),
        default_shadow_configuration(),
        prior_daily_qref=with_future,
        activity_date=current,
    )

    assert compared == baseline


def test_11_building_session_is_point_six_to_point_seven_tref() -> None:
    assert session_dose_range(100.0, "building") == pytest.approx(
        (60.0, 70.0)
    )


def test_12_maintenance_session_is_point_three_to_point_four_tref() -> None:
    assert session_dose_range(100.0, "maintenance") == pytest.approx(
        (30.0, 40.0)
    )


def test_13_ninety_percent_recovery_turns_point_six_into_point_five_four() -> None:
    base = session_dose_range(100.0, "building")[0]

    assert adjust_target_for_recovery(base, 0.90) == pytest.approx(54.0)
    assert weekly_target(100.0, 0.8) == pytest.approx(80.0)


def test_14_direct_spillover_threshold_is_exactly_half_tref() -> None:
    configuration = default_shadow_configuration()
    at_threshold = calculate_shadow_result(
        _empty_analysis(qref={"Z2": 67.5}), configuration
    )
    above_threshold = calculate_shadow_result(
        _empty_analysis(qref={"Z2": 68.5}), configuration
    )
    at_rows = {row["zone"]: row for row in at_threshold["rows"]}
    above_rows = {row["zone"]: row for row in above_threshold["rows"]}

    assert SECONDARY_DIRECT_RATIO_THRESHOLD == pytest.approx(0.50)
    assert direct_ratio(67.5, 135.0) == pytest.approx(0.50)
    assert at_rows["Z1"]["spillover_received"] == pytest.approx(0.0)
    assert above_rows["Z1"]["spillover_received"] == pytest.approx(0.2)
    assert limiting_secondary_zones(
        {"Z1": 0.0, "Z2": 67.5},
        {"Z1": 240.0, "Z2": 135.0},
        primary_zone="Z1",
    ) == ("Z2",)


def test_15_spillover_received_does_not_create_recursive_spillover() -> None:
    result = calculate_shadow_result(
        _empty_analysis(qref={"Z2": 1000.0}),
        default_shadow_configuration(),
    )
    rows = {row["zone"]: row for row in result["rows"]}

    assert rows["Z3"]["spillover_received"] > 0.5 * rows["Z3"]["tref_effective"]
    assert rows["Z3"]["spillover_excess"] == pytest.approx(0.0)
    assert rows["Z4"]["spillover_received"] == pytest.approx(0.0)


def test_16_missing_hr_creates_no_hidden_qref_load() -> None:
    result = normalize_stream_intervals(
        NormalizerInput(
            offsets=[0.0, 1.0],
            metrics={},
            elapsed_time_sec=1.0,
            icu_recording_time_sec=1.0,
        )
    )
    analysis = calculate_onflows_intrazone_load(
        result, default_onflows_zone_profile()
    )

    assert analysis["available"] is False
    assert analysis["hr_coverage_percent"] == pytest.approx(0.0)
    assert analysis["total_qref_sec"] == pytest.approx(0.0)
    assert all(row["qref_seconds"] == 0.0 for row in analysis["zones"])


def test_17_changed_tref_bounds_recalculate_dependent_values() -> None:
    current = date(2026, 8, 9)
    history = _history(current, 40, weekly_qref=120.0)
    baseline = calculate_shadow_result(
        _empty_analysis(qref={"Z2": 75.0}),
        default_shadow_configuration(),
        prior_daily_qref=history,
        activity_date=current,
    )
    changed_configuration = configuration_with_overrides(
        {
            "parameter.Z2.tref_min": 150.0,
            "parameter.Z2.tref_max": 160.0,
        }
    )
    changed = calculate_shadow_result(
        _empty_analysis(qref={"Z2": 75.0}),
        changed_configuration,
        prior_daily_qref=history,
        activity_date=current,
    )
    before = next(row for row in baseline["rows"] if row["zone"] == "Z2")
    after = next(row for row in changed["rows"] if row["zone"] == "Z2")

    assert before["tref_effective"] == pytest.approx(120.0)
    assert after["tref_effective"] == pytest.approx(150.0)
    assert after["direct_ratio"] < before["direct_ratio"]
    assert after["spillover_excess"] < before["spillover_excess"]
