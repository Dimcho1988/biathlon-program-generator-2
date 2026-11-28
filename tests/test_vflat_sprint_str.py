from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

from vflat_b65 import (
    SPRINT_STR_CONFIG_VERSION,
    SPRINT_STR_MODEL_VERSION,
    SprintSTRConfig,
    detect_sprint_str,
)


def _frame(speed_kmh: np.ndarray, grade_pct: np.ndarray) -> pd.DataFrame:
    count = len(speed_kmh)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return pd.DataFrame(
        {
            "timestamp": [start + timedelta(seconds=index) for index in range(count)],
            "block": np.ones(count, dtype=int),
            "speed_raw_kmh": speed_kmh,
            "vflat_b65_kmh": speed_kmh,
            "grade_actual_pct": grade_pct,
            "accel_mps2": np.gradient(speed_kmh / 3.6),
            "valid": np.ones(count, dtype=bool),
        }
    )


def test_flat_short_speed_impulse_is_reported_as_diagnostic_sprint_str() -> None:
    speed = np.full(120, 12.0)
    speed[45:50] = np.linspace(12.0, 28.0, 5)
    speed[50:61] = 28.0
    speed[61:66] = np.linspace(28.0, 12.0, 5)

    result = detect_sprint_str(_frame(speed, np.zeros(120)))

    assert result.summary["model_version"] == SPRINT_STR_MODEL_VERSION
    assert result.summary["config_version"] == SPRINT_STR_CONFIG_VERSION
    assert result.summary["candidate_count"] == 1
    assert result.summary["affects_canonical_load"] is False
    assert result.summary["double_counts_hr_zones"] is False
    assert len(result.intervals) == 1
    assert result.intervals[0]["peak_raw_speed_kmh"] == 28.0
    assert 5.0 <= result.intervals[0]["duration_s"] <= 25.0
    assert any(result.sample_mask)


def test_speed_carried_from_steep_descent_has_no_flat_effort_onset() -> None:
    speed = np.full(120, 12.0)
    speed[35:41] = np.linspace(12.0, 30.0, 6)
    speed[41:65] = 30.0
    speed[65:75] = np.linspace(30.0, 12.0, 10)
    grade = np.zeros(120)
    grade[30:50] = -6.0

    result = detect_sprint_str(_frame(speed, grade))

    assert result.summary["candidate_count"] == 0
    assert not any(result.sample_mask)


def test_sprint_str_configuration_is_strictly_versioned() -> None:
    assert SprintSTRConfig().reference_window_s == 61
    assert SprintSTRConfig().min_grade_pct == -3.0
    try:
        SprintSTRConfig(config_version="retired")
    except ValueError as exc:
        assert "unsupported" in str(exc)
    else:
        raise AssertionError("retired sprint/STR config was accepted")
