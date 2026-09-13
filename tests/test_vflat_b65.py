from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from vflat_b65 import (
    CONFIG_VERSION,
    MODEL_VERSION,
    VFlatB65Config,
    apply_vflat_b65,
    stationary_multiplier_b65,
)


GOLDEN = {
    -8.0: 0.632547476,
    -5.0: 0.632547476,
    -3.0: 0.632547476,
    -2.0: 0.795328534,
    -1.0: 1.0,
    0.0: 1.0,
    1.0: 1.0,
    5.0: 1.919703223,
    6.0: 2.0502080755,
    7.0: 2.274432421,
    8.0: 2.5522655155,
    10.0: 3.0363140485,
    12.0: 3.565691422,
    13.0: 3.848843257,
    14.0: 4.144986457,
    15.0: 4.4545930735,
    18.0: 4.4545930735,
}


def test_locked_versions_defaults_and_golden_multipliers() -> None:
    config = VFlatB65Config()
    assert MODEL_VERSION == "vflat_b65_dynamic_v4_uphill120_memory170"
    assert CONFIG_VERSION == "vflat_b65_config_v4_uphill120_memory170"
    assert config.descent_grade_weight == 1.7
    assert config.descent_grade_horizon_s == 20.0
    assert config.descent_memory_strength_kmh == 0.0
    assert config.speed_smoothing_s == 11
    assert config.transition_anchor_strength == 0.90
    assert config.transition_accel_scale_mps2 == 0.10
    assert config.transition_decay_s == 18.0
    assert config.output_smoothing_s == 21
    grades = np.asarray(list(GOLDEN))
    expected = np.asarray(list(GOLDEN.values()))
    # Approved V3 anchors reduced by 20% only above the flat multiplier.
    expected = np.where(grades > 1.0, 1.0 + 0.8 * (expected - 1.0), expected)
    assert stationary_multiplier_b65(grades, config) == pytest.approx(
        expected, abs=1e-9
    )


@pytest.mark.parametrize("boundary", (-3.0, -1.0, 1.0, 5.0, 8.0, 15.0))
def test_stationary_curve_is_continuous_at_locked_boundaries(boundary: float) -> None:
    # The accepted base curve uses a 0.53 power immediately above +1%, so its
    # derivative is unbounded at the boundary even though the value is
    # continuous.  Use a limit-scale epsilon rather than a slope assertion.
    epsilon = 1e-12
    values = stationary_multiplier_b65(
        np.asarray([boundary - epsilon, boundary, boundary + epsilon])
    )
    assert abs(values[1] - values[0]) < 1e-6
    assert abs(values[2] - values[1]) < 1e-6


def test_stationary_cap_does_not_cap_actual_grade_memory_or_mutate_raw() -> None:
    grade = np.r_[np.full(20, -20.0), np.zeros(25), np.full(20, 20.0), np.zeros(25)]
    source = pd.DataFrame(
        {
            "grade_pct": grade,
            "speed_mps": np.full(len(grade), 5.0),
            "accel_mps2": np.zeros(len(grade)),
            "block": np.ones(len(grade), dtype=int),
            "turn_flag": np.zeros(len(grade), dtype=bool),
        }
    )
    before = source.copy(deep=True)
    result = apply_vflat_b65(source)
    pd.testing.assert_frame_equal(source, before)
    assert set(result.loc[:19, "grade_stationary_pct"]) == {-3.0}
    assert set(result.loc[45:64, "grade_stationary_pct"]) == {15.0}
    assert set(result.loc[:19, "grade_actual_pct"]) == {-20.0}
    assert set(result.loc[45:64, "grade_actual_pct"]) == {20.0}
    assert result.loc[20, "grade_effective_pct"] == pytest.approx(-34.0)
    assert (result.descent_memory_term_kmh == 0.0).all()
    assert result.loc[65, "climb_memory_term_kmh"] > 0.0


def test_no_time_shift_and_parallel_fields_are_present() -> None:
    source = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=90, freq="s", tz="UTC"),
            "grade_pct": np.linspace(-3.0, 15.0, 90),
            "speed_mps": np.full(90, 5.0),
            "accel_mps2": np.zeros(90),
            "block": np.ones(90, dtype=int),
            "turn_flag": np.zeros(90, dtype=bool),
        }
    )
    result = apply_vflat_b65(source)
    assert result.index.equals(source.index)
    assert result.timestamp.equals(source.timestamp)
    assert {
        "speed_raw_kmh",
        "vflat_b65_kmh",
        "vflat_delta_kmh",
        "grade_actual_pct",
        "grade_stationary_pct",
        "vflat_model_version",
    } <= set(result)


def test_transition_anchor_only_follows_accelerating_descent_entry() -> None:
    grade = np.r_[np.zeros(20), np.full(25, -4.0), np.zeros(20)]
    accel = np.zeros(len(grade))
    accel[20] = 0.2
    source = pd.DataFrame(
        {
            "grade_pct": grade,
            "speed_mps": np.full(len(grade), 5.0),
            "accel_mps2": accel,
            "block": np.ones(len(grade), dtype=int),
            "turn_flag": np.zeros(len(grade), dtype=bool),
        }
    )

    result = apply_vflat_b65(source)

    assert result.loc[20, "transition_weight"] == pytest.approx(1.0)
    assert 0.0 < result.loc[37, "transition_weight"] < 0.1
    assert result.loc[38, "transition_weight"] == pytest.approx(0.0)
    assert result.loc[19, "transition_weight"] == 0.0


def test_flat_sprint_does_not_activate_transition_anchor() -> None:
    accel = np.zeros(50)
    accel[20:24] = 0.2
    source = pd.DataFrame(
        {
            "grade_pct": np.zeros(50),
            "speed_mps": np.r_[np.full(20, 3.0), np.full(15, 8.0), np.full(15, 3.0)],
            "accel_mps2": accel,
            "block": np.ones(50, dtype=int),
            "turn_flag": np.zeros(50, dtype=bool),
        }
    )

    result = apply_vflat_b65(source)

    assert np.all(result["transition_weight"].to_numpy() == 0.0)
    assert result.loc[25, "vflat_b65_kmh"] > 20.0


def _prepared(grade):
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=len(grade), freq="s", tz="UTC"),
        "grade_pct": grade, "speed_raw_mps": 5.0, "speed_mps": 5.0,
        "accel_mps2": 0.0, "block": 0, "turn_flag": False,
    })


@pytest.mark.parametrize("timestamp_unit", ("ns", "us", "s"))
def test_v4_tracks_current_grade_and_returns_exactly_at_20_seconds(timestamp_unit):
    source = _prepared(np.r_[np.full(20, -6.0), np.linspace(1.0, 10.0, 30)])
    source["timestamp"] = source.timestamp.dt.as_unit(timestamp_unit)
    out = apply_vflat_b65(source)
    assert out.loc[20, "grade_effective_pct"] == pytest.approx(-9.2)
    assert out.loc[20, "grade_stationary_pct"] == -3.0
    w10 = (np.exp(-10 / 18) - np.exp(-20 / 18)) / (1 - np.exp(-20 / 18))
    assert out.loc[30, "grade_effective_pct"] == pytest.approx(source.loc[30, "grade_pct"] - 10.2 * w10)
    assert out.loc[39, "grade_effective_pct"] < source.loc[39, "grade_pct"]
    assert out.loc[40, "grade_effective_pct"] == source.loc[40, "grade_pct"]
    assert (out.loc[40:, "descent_grade_memory_weight"] == 0.0).all()
    assert out.loc[20, "descent_grade_reference_pct"] == -6.0


def test_v4_stop_uses_unsmoothed_speed_and_requires_a_new_moving_descent():
    grade = np.r_[np.full(20, -6.0), np.full(10, 10.0), np.full(10, -6.0),
                  np.full(10, 3.0), np.full(15, -5.0), np.full(25, 4.0)]
    source = _prepared(grade)
    source.loc[30:33, "speed_raw_mps"] = 0.0
    out = apply_vflat_b65(source)
    assert out.loc[20, "grade_effective_pct"] == pytest.approx(-0.2)
    assert not out.loc[32, "descent_stop_locked"]
    assert out.loc[33, "descent_stop_locked"]
    assert out.loc[40, "descent_grade_memory_weight"] == 0.0
    assert out.loc[40, "grade_effective_pct"] == 3.0
    assert not out.loc[50, "descent_stop_locked"]
    assert out.loc[65, "descent_grade_reference_pct"] == -5.0
    assert out.loc[65, "grade_effective_pct"] == pytest.approx(-4.5)


def test_v4_stop_cancels_an_active_tail_at_confirmation_not_retroactively():
    source = _prepared(np.r_[np.full(20, -6.0), np.full(30, 10.0)])
    source.loc[22:25, "speed_raw_mps"] = 0.0
    out = apply_vflat_b65(source)
    assert out.loc[24, "descent_grade_memory_weight"] > 0.0
    assert (out.loc[25:, "descent_grade_memory_weight"] == 0.0).all()


def test_v4_reference_uses_only_prior_15_seconds_and_resets_at_recording_gaps():
    source = _prepared(np.r_[np.full(10, -20.0), np.full(15, -4.0), np.full(30, 10.0)])
    source.index = np.arange(len(source)) * 3 + 7  # No RangeIndex assumption.
    out = apply_vflat_b65(source)
    assert out.iloc[25].descent_grade_reference_pct == -4.0
    source.loc[source.index[28:], "block"] = 1
    out = apply_vflat_b65(source)
    assert out.iloc[27].descent_grade_memory_weight > 0
    assert (out.iloc[28:].descent_grade_memory_weight == 0).all()
    source["block"] = 0
    source.loc[source.index[28:], "timestamp"] += pd.Timedelta(seconds=60)
    out = apply_vflat_b65(source)
    assert (out.iloc[28:].descent_grade_memory_weight == 0).all()


def test_v4_reentry_cancels_tail_and_next_exit_starts_a_new_window():
    source = _prepared(np.r_[np.full(20, -6.0), np.full(5, 4.0), np.full(20, -8.0), np.full(25, 5.0)])
    out = apply_vflat_b65(source)
    assert out.loc[24, "descent_grade_memory_weight"] > 0
    assert (out.loc[25:44, "descent_grade_memory_weight"] == 0).all()
    assert out.loc[45, "descent_grade_reference_pct"] == -8.0
    assert out.loc[45, "descent_grade_age_s"] == 0.0


def test_v4_does_not_double_apply_the_old_speed_memory():
    with pytest.raises(ValueError, match="speed subtraction"):
        VFlatB65Config(descent_memory_strength_kmh=20.0)
