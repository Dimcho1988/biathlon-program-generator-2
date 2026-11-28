"""Exact parity with scalar HR smoothing, including window and batch edges."""
import numpy as np
import pytest

from hrmod_lab import wave_detection_v4 as detection
from hrmod_lab.schemas import HRmodConfig


@pytest.mark.parametrize("count", [0, 1, 2, 3, 511, 513, 1031])
@pytest.mark.parametrize("spacing", ["regular", "sparse", "irregular", "duplicates"])
def test_batched_fits_equal_scalar_fits(count, spacing):
    rng = np.random.default_rng(849)
    increments = {"regular": np.ones(count), "sparse": np.full(count, 4.),
        "irregular": rng.choice([.01, .2, .8, 1., 4., 12.], count),
        "duplicates": np.zeros(count)}[spacing]
    times = np.cumsum(increments)
    values = np.round(130 + 20 * np.sin(times / 30) + rng.normal(0, 2, count))
    config = HRmodConfig()
    actual = detection._weighted_local_lines(times, values, config)
    expected = [detection._weighted_local_line(times, values, float(t), config) for t in times]
    for index, array in enumerate(actual):
        np.testing.assert_array_equal(array, [row[index] for row in expected])


@pytest.mark.parametrize("method,iterations", [("local_linear", 0), ("robust_local_linear", 0), ("robust_local_linear", 4)])
@pytest.mark.parametrize("window,min_points", [(1., 7), (5., 3), (150., 3)])
def test_custom_windows_and_robust_stopping_match_scalar(method, iterations, window, min_points):
    times = np.arange(300, dtype=float)
    # Flat, linear, isolated spike and plateau sections stop at different iterations.
    values = np.r_[np.full(80, 140.), np.linspace(140, 175, 40), np.full(60, 175.), np.linspace(175, 120, 120)]
    values[30] = 190
    config = HRmodConfig(smoothing_method=method, smoothing_robust_iterations=iterations,
                        smoothing_window_s=window, smoothing_min_points=min_points)
    actual = detection._weighted_local_lines(times, values, config)
    expected = [detection._weighted_local_line(times, values, float(t), config) for t in times]
    for index, array in enumerate(actual):
        np.testing.assert_array_equal(array, [row[index] for row in expected])


def test_gaps_missing_values_and_edge_flags_match_scalar(monkeypatch):
    times = np.arange(1200, dtype=float)
    values = 140 + 25 * np.sin(times / 30)
    values[200:210] = np.nan
    segments = np.where(times < 600, 0, 1)
    segments[590:610] = -1
    args = dict(elapsed_s=times, clean_hr=values, segment_ids=segments, config=HRmodConfig())
    actual = detection._detection_signal(**args)
    def scalar(t, y, config):
        return tuple(np.asarray(column) for column in zip(*[
            detection._weighted_local_line(t, y, float(center), config) for center in t], strict=True))
    monkeypatch.setattr(detection, "_weighted_local_lines", scalar)
    expected = detection._detection_signal(**args)
    for optimized, reference in zip(actual, expected, strict=True):
        np.testing.assert_array_equal(optimized, reference)
