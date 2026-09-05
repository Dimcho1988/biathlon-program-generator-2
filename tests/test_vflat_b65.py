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
    5.0: 1.613135482,
    6.0: 1.700138717,
    7.0: 1.849621614,
    8.0: 2.034843677,
    10.0: 2.357542699,
    12.0: 2.710460948,
    13.0: 2.899228838,
    14.0: 3.096657638,
    15.0: 3.303062049,
    18.0: 3.303062049,
}


def test_locked_versions_defaults_and_golden_multipliers() -> None:
    config = VFlatB65Config()
    assert MODEL_VERSION == "vflat_b65_dynamic_v3"
    assert CONFIG_VERSION == "vflat_b65_config_v3"
    assert config.speed_smoothing_s == 11
    assert config.transition_anchor_strength == 0.90
    assert config.transition_accel_scale_mps2 == 0.10
    assert config.transition_decay_s == 18.0
    assert config.output_smoothing_s == 21
    grades = np.asarray(list(GOLDEN))
    expected = np.asarray(list(GOLDEN.values()))
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
    assert result.loc[20, "descent_memory_term_kmh"] < 0.0
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
