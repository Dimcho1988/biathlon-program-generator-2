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
    DEFAULT_PLANNING_SETTINGS,
    SECONDARY_DIRECT_RATIO_THRESHOLD,
    adjust_target_for_recovery,
    direct_ratio,
    limiting_secondary_zones,
    session_dose_range,
    validate_planning_settings,
    weekly_target,
)
from intervals_inspector.shadow_model import (
    calculate_shadow_result,
    default_shadow_configuration,
)
from intervals_inspector.stream_normalizer import (
    NormalizerInput,
    normalize_stream_intervals,
)


EXPECTED_TREF_MINUTES = {
    "Z1": 300.0,
    "Z2": 180.0,
    "Z3": 70.0,
    "Z4": 20.0,
    "Z5": 20.0,
}


def _empty_analysis(
    *,
    equivalent_time: dict[str, float] | None = None,
    legacy_qref: dict[str, float] | None = None,
):
    configuration = default_shadow_configuration()
    equivalent_time = equivalent_time or {}
    legacy_qref = legacy_qref or {}
    return {
        "hr_coverage_percent": 100.0,
        "zones": [
            {
                "zone": zone.zone,
                "real_seconds": 0.0,
                "equivalent_seconds": 60.0
                * float(equivalent_time.get(zone.zone, 0.0)),
                # Deliberately accepted only as a deprecated input alias.
                "qref_seconds": 60.0
                * float(legacy_qref.get(zone.zone, 0.0)),
            }
            for zone in configuration.zones
        ],
    }


def _history(
    current: date,
    days: int,
    *,
    weekly_equivalent_time: float,
    zone: str = "Z2",
):
    daily = weekly_equivalent_time / 7.0
    return [
        {
            "date": (current - timedelta(days=days - index)).isoformat(),
            zone: daily,
        }
        for index in range(days)
    ]


def _rows(result):
    return {row["zone"]: row for row in result["rows"]}


def test_01_fixed_tref_is_the_exact_expert_setting_for_every_zone() -> None:
    configuration = default_shadow_configuration()
    result = calculate_shadow_result(_empty_analysis(), configuration)
    rows = _rows(result)

    assert {zone.zone: zone.tref_minutes for zone in configuration.zones} == (
        EXPECTED_TREF_MINUTES
    )
    assert {
        zone: row["tref_effective"] for zone, row in rows.items()
    } == EXPECTED_TREF_MINUTES
    assert all(
        row["tref_source"] == "initial expert setting"
        for row in rows.values()
    )
    assert all(row["h40_equivalent_minutes"] is None for row in rows.values())


@pytest.mark.parametrize("historical", (20.0, 180.0, 900.0))
def test_02_h40_is_diagnostic_and_never_changes_fixed_tref(
    historical: float,
) -> None:
    current = date(2026, 8, 9)
    result = calculate_shadow_result(
        _empty_analysis(),
        default_shadow_configuration(),
        prior_daily_effective_load=_history(
            current,
            40,
            weekly_equivalent_time=historical,
        ),
        activity_date=current,
    )
    row = _rows(result)["Z2"]

    assert row["h40_equivalent_minutes"] == pytest.approx(historical)
    assert row["tref_effective"] == pytest.approx(180.0)
    assert row["tref_source"] == "initial expert setting"
    assert row["tref_history_days"] == 40


@pytest.mark.parametrize("days", (7, 20, 39))
def test_03_partial_history_reports_hn_without_modifying_tref(days: int) -> None:
    current = date(2026, 8, 9)
    result = calculate_shadow_result(
        _empty_analysis(),
        default_shadow_configuration(),
        prior_daily_effective_load=_history(
            current,
            days,
            weekly_equivalent_time=120.0,
        ),
        activity_date=current,
    )
    row = _rows(result)["Z2"]

    assert row["h40_equivalent_minutes"] == pytest.approx(120.0)
    assert row["tref_effective"] == pytest.approx(180.0)
    assert row["tref_history_days"] == days
    assert result["history_days"] == days
    assert any(
        warning["id"] == "warning.incomplete_history"
        for warning in result["warnings"]
    )


def test_04_rest_days_are_zero_calendar_days_in_h40_diagnostic() -> None:
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
        prior_daily_effective_load=history,
        activity_date=current,
    )
    row = _rows(result)["Z2"]

    assert row["tref_history_days"] == 10
    assert row["h40_equivalent_minutes"] == pytest.approx(70.0)
    assert row["tref_effective"] == pytest.approx(180.0)


def test_05_current_and_future_data_are_excluded_from_h40() -> None:
    current = date(2026, 8, 9)
    past = _history(
        current,
        40,
        weekly_equivalent_time=120.0,
    )
    with_current_and_future = [
        *past,
        {"date": current.isoformat(), "Z2": 10000.0},
        {
            "date": (current + timedelta(days=1)).isoformat(),
            "Z2": 10000.0,
        },
    ]
    baseline = calculate_shadow_result(
        _empty_analysis(),
        default_shadow_configuration(),
        prior_daily_effective_load=past,
        activity_date=current,
    )
    compared = calculate_shadow_result(
        _empty_analysis(),
        default_shadow_configuration(),
        prior_daily_effective_load=with_current_and_future,
        activity_date=current,
    )

    assert compared == baseline
    assert compared["current_day_excluded"] is True


def test_06_one_equivalent_time_drives_ratio_cascade_spillover_and_effect() -> None:
    result = calculate_shadow_result(
        _empty_analysis(equivalent_time={"Z2": 100.0}),
        default_shadow_configuration(),
    )
    rows = _rows(result)

    assert rows["Z2"]["T_eq_z"] == pytest.approx(100.0)
    assert rows["Z2"]["direct_ratio"] == pytest.approx(100.0 / 180.0)
    assert rows["Z2"]["spillover_excess"] == pytest.approx(10.0)
    assert rows["Z1"]["cascade"] == pytest.approx(100.0)
    assert rows["Z1"]["spillover_received"] == pytest.approx(2.0)
    assert rows["Z3"]["spillover_received"] == pytest.approx(1.0)
    assert rows["Z1"]["E_z"] == pytest.approx(102.0)
    assert rows["Z2"]["E_z"] == pytest.approx(100.0)
    assert rows["Z3"]["E_z"] == pytest.approx(1.0)


def test_07_explicit_equivalent_time_is_authoritative_over_legacy_alias() -> None:
    result = calculate_shadow_result(
        _empty_analysis(
            equivalent_time={"Z2": 0.0},
            legacy_qref={"Z2": 1000.0},
        ),
        default_shadow_configuration(),
    )

    row = _rows(result)["Z2"]
    assert row["T_eq_z"] == pytest.approx(0.0)
    assert row["direct_ratio"] == pytest.approx(0.0)
    assert row["spillover_excess"] == pytest.approx(0.0)


def test_08_direct_spillover_threshold_is_exactly_half_fixed_tref() -> None:
    configuration = default_shadow_configuration()
    at_threshold = calculate_shadow_result(
        _empty_analysis(equivalent_time={"Z2": 90.0}),
        configuration,
    )
    above_threshold = calculate_shadow_result(
        _empty_analysis(equivalent_time={"Z2": 91.0}),
        configuration,
    )
    at_rows = _rows(at_threshold)
    above_rows = _rows(above_threshold)

    assert SECONDARY_DIRECT_RATIO_THRESHOLD == pytest.approx(0.50)
    assert direct_ratio(90.0, 180.0) == pytest.approx(0.50)
    assert at_rows["Z1"]["spillover_received"] == pytest.approx(0.0)
    assert above_rows["Z1"]["spillover_received"] == pytest.approx(0.2)
    assert limiting_secondary_zones(
        {"Z1": 0.0, "Z2": 90.0},
        {"Z1": 300.0, "Z2": 180.0},
        primary_zone="Z1",
    ) == ("Z2",)


def test_09_spillover_received_does_not_create_recursive_spillover() -> None:
    result = calculate_shadow_result(
        _empty_analysis(equivalent_time={"Z2": 1000.0}),
        default_shadow_configuration(),
    )
    rows = _rows(result)

    assert rows["Z3"]["spillover_received"] > 0.5 * rows["Z3"]["tref_effective"]
    assert rows["Z3"]["spillover_excess"] == pytest.approx(0.0)
    assert rows["Z4"]["spillover_received"] == pytest.approx(0.0)


def test_10_missing_hr_creates_no_hidden_equivalent_time() -> None:
    interval_result = normalize_stream_intervals(
        NormalizerInput(
            offsets=[0.0, 1.0],
            metrics={},
            elapsed_time_sec=1.0,
            icu_recording_time_sec=1.0,
        )
    )
    analysis = calculate_onflows_intrazone_load(
        interval_result,
        default_onflows_zone_profile(),
    )

    assert analysis["available"] is False
    assert analysis["hr_coverage_percent"] == pytest.approx(0.0)
    assert analysis["total_equivalent_sec"] == pytest.approx(0.0)
    assert all(
        row["equivalent_seconds"] == pytest.approx(0.0)
        for row in analysis["zones"]
    )


def test_11_building_session_uses_point_six_to_point_seven_tref() -> None:
    assert session_dose_range(180.0, "building") == pytest.approx(
        (108.0, 126.0)
    )


def test_12_maintenance_session_uses_point_three_to_point_four_tref() -> None:
    assert session_dose_range(70.0, "maintenance") == pytest.approx(
        (21.0, 28.0)
    )


def test_13_recovery_and_weekly_targets_apply_each_multiplier_once() -> None:
    base = session_dose_range(180.0, "building")[0]

    assert adjust_target_for_recovery(base, 0.90) == pytest.approx(97.2)
    assert weekly_target(300.0, 0.8) == pytest.approx(240.0)


def test_14_planning_settings_validate_the_complete_default_contract() -> None:
    assert validate_planning_settings(DEFAULT_PLANNING_SETTINGS) == (
        DEFAULT_PLANNING_SETTINGS
    )

    with pytest.raises(ValueError, match="weekly_accent"):
        validate_planning_settings(
            {**DEFAULT_PLANNING_SETTINGS, "weekly_accent": 2.51}
        )
    with pytest.raises(ValueError, match="building_low"):
        validate_planning_settings(
            {
                **DEFAULT_PLANNING_SETTINGS,
                "building_low": 0.8,
                "building_high": 0.7,
            }
        )


@pytest.mark.parametrize(
    ("call", "message"),
    (
        (lambda: direct_ratio(1.0, 0.0), "tref must be positive"),
        (lambda: weekly_target(100.0, 2.51), "must not exceed"),
        (
            lambda: adjust_target_for_recovery(100.0, 1.01),
            "between 0 and 1",
        ),
        (
            lambda: session_dose_range(100.0, "unknown"),
            "building or maintenance",
        ),
    ),
)
def test_15_planning_helpers_reject_invalid_inputs(call, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        call()
