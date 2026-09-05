from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from biathlon.constants import (
    AEROBIC_TREF_BOUNDS_MINUTES,
    COMPONENTS,
    FIXED_STRENGTH_TREF_MINUTES,
    fresh_parameters,
)
from biathlon.physiology import (
    compute_daily_load_history,
    compute_load_statistics,
    compute_readiness_history,
    rolling_load_statistics,
)
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


EXPECTED_TREF_BOUNDS = dict(AEROBIC_TREF_BOUNDS_MINUTES)
EXPECTED_INITIAL_TREF = {
    zone: upper for zone, (_lower, upper) in EXPECTED_TREF_BOUNDS.items()
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
    weekly_effective_load: float,
    zone: str = "Z2",
):
    daily = weekly_effective_load / 7.0
    return [
        {
            "date": (current - timedelta(days=days - index)).isoformat(),
            zone: daily,
        }
        for index in range(days)
    ]


def _rows(result):
    return {row["zone"]: row for row in result["rows"]}


def test_01_no_history_uses_the_expert_upper_bound_for_every_zone() -> None:
    configuration = default_shadow_configuration()
    result = calculate_shadow_result(_empty_analysis(), configuration)
    rows = _rows(result)

    assert {
        zone.zone: (zone.tref_min, zone.tref_max)
        for zone in configuration.zones
    } == EXPECTED_TREF_BOUNDS
    assert {
        zone: row["tref_effective"] for zone, row in rows.items()
    } == EXPECTED_INITIAL_TREF
    assert all(
        row["tref_source"] == "expert upper-bound fallback"
        for row in rows.values()
    )
    assert all(row["tref_bound_applied"] == "none" for row in rows.values())
    assert all(row["h40_equivalent_minutes"] is None for row in rows.values())


@pytest.mark.parametrize(
    ("raw_tref", "expected_tref", "expected_bound"),
    (
        (20.0, 90.0, "lower"),
        (120.0, 120.0, "none"),
        (900.0, 180.0, "upper"),
    ),
)
def test_02_real_40_day_tref_is_clamped_to_the_expert_bounds(
    raw_tref: float,
    expected_tref: float,
    expected_bound: str,
) -> None:
    current = date(2026, 8, 9)
    result = calculate_shadow_result(
        _empty_analysis(),
        default_shadow_configuration(),
        prior_daily_effective_load=_history(
            current,
            40,
            weekly_effective_load=raw_tref,
        ),
        activity_date=current,
    )
    row = _rows(result)["Z2"]

    assert row["h40_equivalent_minutes"] == pytest.approx(raw_tref)
    assert row["tref_raw"] == pytest.approx(raw_tref)
    assert row["tref_effective"] == pytest.approx(expected_tref)
    assert row["tref_bound_applied"] == expected_bound
    assert row["tref_source"] == "40-day history"
    assert row["tref_history_days"] == 40


@pytest.mark.parametrize("days", (7, 20, 39))
def test_03_partial_history_produces_a_provisional_bounded_tref(days: int) -> None:
    current = date(2026, 8, 9)
    result = calculate_shadow_result(
        _empty_analysis(),
        default_shadow_configuration(),
        prior_daily_effective_load=_history(
            current,
            days,
            weekly_effective_load=120.0,
        ),
        activity_date=current,
    )
    row = _rows(result)["Z2"]

    assert row["h40_equivalent_minutes"] == pytest.approx(120.0)
    assert row["tref_effective"] == pytest.approx(120.0)
    assert row["tref_source"] == "provisional history"
    assert row["tref_bound_applied"] == "none"
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
    assert row["tref_effective"] == pytest.approx(90.0)
    assert row["tref_bound_applied"] == "lower"


def test_05_current_and_future_data_are_excluded_from_h40() -> None:
    current = date(2026, 8, 9)
    past = _history(
        current,
        40,
        weekly_effective_load=120.0,
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


def test_08_direct_spillover_threshold_uses_half_the_effective_tref() -> None:
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


def test_16_daily_tref_is_causal_but_current_tref_includes_latest_day() -> None:
    parameters = fresh_parameters()
    rows = []
    for day, z2_load in enumerate((30.0, 10.0, 50.0), start=1):
        row = {"date": pd.Timestamp(2026, 8, day)}
        for component in COMPONENTS:
            row[f"q_{component}"] = z2_load if component == "Z2" else 0.0
        rows.append(row)

    daily = compute_daily_load_history(pd.DataFrame(rows), parameters)

    # Day 1 has no completed history. Day 2 sees only day 1, and day 3 sees
    # only days 1-2. The current/planning value after day 3 includes day 3.
    assert daily.loc[pd.Timestamp("2026-08-01"), "tref_used_Z2"] == pytest.approx(
        180.0
    )
    assert daily.loc[pd.Timestamp("2026-08-02"), "tref_used_Z2"] == pytest.approx(
        180.0
    )
    assert daily.loc[pd.Timestamp("2026-08-03"), "tref_used_Z2"] == pytest.approx(
        140.0
    )
    without_persisted_tref = daily.drop(
        columns=[f"tref_used_{component}" for component in COMPONENTS]
    )
    rolling = rolling_load_statistics(without_persisted_tref, parameters)
    assert rolling.loc[
        rolling["component"] == "Z2", "Tref"
    ].to_numpy() == pytest.approx((180.0, 180.0, 140.0))
    current = compute_load_statistics(
        daily, parameters, as_of=pd.Timestamp("2026-08-03")
    )
    assert current.loc["Z2", "Tref"] == pytest.approx(180.0)


def test_17_recovery_reuses_causal_aerobic_tref_and_fixed_strength_tref() -> None:
    parameters = fresh_parameters()
    rows = []
    for day in range(1, 4):
        row = {"date": pd.Timestamp(2026, 8, day)}
        for component in COMPONENTS:
            row[f"q_{component}"] = (
                20.0 if component in {"Z2", "STR"} else 0.0
            )
        rows.append(row)

    daily = compute_daily_load_history(pd.DataFrame(rows), parameters)
    readiness = compute_readiness_history(
        daily, parameters, use_supplied_tref=True
    )

    for component in COMPONENTS:
        actual = readiness.loc[
            readiness["component"] == component, "Tref"
        ].to_numpy()
        expected = daily[f"tref_used_{component}"].to_numpy()
        assert actual == pytest.approx(expected)
    assert daily["tref_used_STR"].to_numpy() == pytest.approx(
        FIXED_STRENGTH_TREF_MINUTES
    )


def test_18_tref_window_is_fixed_at_40_days_not_a_mutable_7_40_setting() -> None:
    parameters = fresh_parameters()
    parameters["long_window_days"] = 2
    rows = []
    for day, z2_load in enumerate((30.0, 30.0, 0.0, 0.0), start=1):
        row = {"date": pd.Timestamp(2026, 8, day)}
        for component in COMPONENTS:
            row[f"q_{component}"] = z2_load if component == "Z2" else 0.0
        rows.append(row)

    daily = compute_daily_load_history(pd.DataFrame(rows), parameters)
    assert daily.loc[pd.Timestamp("2026-08-04"), "tref_used_Z2"] == pytest.approx(
        140.0
    )
    current = compute_load_statistics(
        daily, parameters, as_of=pd.Timestamp("2026-08-04")
    )
    assert current.loc["Z2", "Tref"] == pytest.approx(105.0)
