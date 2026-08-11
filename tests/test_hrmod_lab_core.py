from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import inspect
import math

import pytest

from hrmod_lab.hrmod_core import compute_hrmod_hr_only
from hrmod_lab.schemas import (
    AthleteHRProfile,
    HRmodConfig,
    HRSample,
    HRZone,
)


START = datetime(2026, 1, 1, tzinfo=UTC)


def _profile(
    *,
    floor: float = 50.0,
    hrmax: float = 200.0,
    boundaries: tuple[float, ...] = (50.0, 100.0, 120.0, 140.0, 160.0, 200.0),
) -> AthleteHRProfile:
    assert boundaries[0] == floor
    assert boundaries[-1] == hrmax
    return AthleteHRProfile(
        hrmax_bpm=hrmax,
        hr_floor_bpm=floor,
        zones=tuple(
            HRZone(f"Z{index + 1}", boundaries[index], boundaries[index + 1])
            for index in range(5)
        ),
    )


def _samples(values: list[float | None], times_s: list[float] | None = None) -> tuple[HRSample, ...]:
    if times_s is None:
        times_s = [float(index) for index in range(len(values))]
    return tuple(
        HRSample(START + timedelta(seconds=elapsed), value)
        for elapsed, value in zip(times_s, values, strict=True)
    )


def _forward_first_order_response(
    *,
    duration_s: int = 360,
    baseline_bpm: float = 100.0,
    pulse_bpm: float = 155.0,
    pulse_start_s: int = 40,
    pulse_end_s: int = 75,
    tau_on_s: float = 18.0,
    tau_off_s: float = 28.0,
) -> list[float]:
    observed = [baseline_bpm]
    for second in range(1, duration_s):
        target = pulse_bpm if pulse_start_s <= second < pulse_end_s else baseline_bpm
        tau = tau_on_s if target >= observed[-1] else tau_off_s
        gain = 1.0 - math.exp(-1.0 / tau)
        observed.append(observed[-1] + gain * (target - observed[-1]))
    return observed


def _episode_config(**overrides: object) -> HRmodConfig:
    values: dict[str, object] = {
        "delay_s": 0.0,
        "tau_on_s": 18.0,
        "tau_off_s": 28.0,
        "smoothing_window_s": 7.0,
        "smoothing_min_points": 3,
        "correction_deadband_bpm": 0.01,
        "min_lobe_duration_s": 1.0,
        "min_lobe_area_bpm_s": 0.01,
        "episode_neutral_gap_s": 12.0,
        "episode_balance_tolerance_bpm_s": 10.0,
        "long_gap_threshold_s": 10.0,
    }
    values.update(overrides)
    return HRmodConfig(**values)


def _corrected_episodes(result):
    return [episode for episode in result.episode_summary if episode.corrected]


def test_core_signature_and_schema_have_no_reference_inputs() -> None:
    parameters = inspect.signature(compute_hrmod_hr_only).parameters
    assert tuple(parameters) == ("hr_samples", "athlete_profile", "config")
    forbidden = {"speed", "power", "grade", "distance", "cadence", "laps", "intervals"}
    assert forbidden.isdisjoint(HRSample.__dataclass_fields__)
    with pytest.raises(TypeError):
        compute_hrmod_hr_only(  # type: ignore[call-arg]
            hr_samples=_samples([100.0] * 20),
            athlete_profile=_profile(),
            speed=[1.0] * 20,
        )


def test_alpha_zero_returns_clean_hr_exactly() -> None:
    config = replace(_episode_config(), alpha=0.0)
    result = compute_hrmod_hr_only(
        hr_samples=_samples(_forward_first_order_response()),
        athlete_profile=_profile(),
        config=config,
    )
    assert [point.hrmod_bpm for point in result.timeseries] == [
        point.clean_hr_bpm for point in result.timeseries
    ]
    assert all(point.added_correction_bpm == 0.0 for point in result.timeseries)
    assert all(point.removed_correction_bpm == 0.0 for point in result.timeseries)


def test_constant_hr_creates_no_correction() -> None:
    result = compute_hrmod_hr_only(
        hr_samples=_samples([125.0] * 120),
        athlete_profile=_profile(),
        config=_episode_config(),
    )
    assert not _corrected_episodes(result)
    assert all(point.hrmod_bpm == pytest.approx(125.0) for point in result.timeseries)
    assert all(abs(point.added_correction_bpm) < 1e-12 for point in result.timeseries)
    assert all(abs(point.removed_correction_bpm) < 1e-12 for point in result.timeseries)


def test_short_missing_hr_and_spike_artifact_are_transparently_interpolated() -> None:
    values = [100.0, 101.0, 180.0, 103.0, 104.0, None, 106.0, 107.0]
    result = compute_hrmod_hr_only(
        hr_samples=_samples(values),
        athlete_profile=_profile(),
        config=_episode_config(smoothing_min_points=2, smoothing_window_s=5.0),
    )
    spike = result.timeseries[2]
    missing = result.timeseries[5]
    assert spike.raw_hr_bpm == 180.0
    assert spike.clean_hr_bpm == pytest.approx(102.0)
    assert {"HR_ARTIFACT", "INTERPOLATED_HR"}.issubset(spike.quality_flags)
    assert missing.raw_hr_bpm is None
    assert missing.clean_hr_bpm == pytest.approx(105.0)
    assert "INTERPOLATED_HR" in missing.quality_flags
    assert result.diagnostics.artifact_samples == 1
    assert result.diagnostics.interpolated_samples == 2
    assert "HR_ARTIFACTS_PRESENT" in result.diagnostics.flags
    assert "INTERPOLATED_HR" in result.diagnostics.flags


def test_rise_and_fall_move_equal_area_earlier_and_later() -> None:
    result = compute_hrmod_hr_only(
        hr_samples=_samples(_forward_first_order_response()),
        athlete_profile=_profile(),
        config=_episode_config(),
    )
    episodes = _corrected_episodes(result)
    assert episodes
    for episode in episodes:
        assert episode.added_area_bpm_s == pytest.approx(
            episode.removed_area_bpm_s, abs=1e-6
        )
        assert abs(episode.area_balance_error_bpm_s) <= 1e-6

    added = [
        (point.elapsed_s, point.added_correction_bpm * point.dt_s)
        for point in result.timeseries
        if point.added_correction_bpm > 0.0
    ]
    removed = [
        (point.elapsed_s, point.removed_correction_bpm * point.dt_s)
        for point in result.timeseries
        if point.removed_correction_bpm > 0.0
    ]
    assert added and removed
    added_center = sum(time * area for time, area in added) / sum(area for _, area in added)
    removed_center = sum(time * area for time, area in removed) / sum(area for _, area in removed)
    assert added_center < removed_center


def test_hrmod_bounds_and_capacity_limit_share_the_same_moved_area() -> None:
    values = _forward_first_order_response(
        baseline_bpm=100.0,
        pulse_bpm=118.0,
        pulse_start_s=40,
        pulse_end_s=75,
    )
    profile = _profile(
        floor=99.0,
        hrmax=120.0,
        boundaries=(99.0, 103.0, 107.0, 111.0, 115.0, 120.0),
    )
    result = compute_hrmod_hr_only(
        hr_samples=_samples(values),
        athlete_profile=profile,
        config=_episode_config(max_addition_bpm=0.25),
    )
    finite_hrmod = [point.hrmod_bpm for point in result.timeseries if point.hrmod_bpm is not None]
    assert min(finite_hrmod) >= profile.hr_floor_bpm - 1e-9
    assert max(finite_hrmod) <= profile.hrmax_bpm + 1e-9
    episodes = _corrected_episodes(result)
    assert episodes
    assert any(episode.capacity_limited_area_bpm_s > 0.0 for episode in episodes)
    for episode in episodes:
        assert episode.moved_area_bpm_s <= episode.positive_capacity_bpm_s + 1e-6
        assert episode.moved_area_bpm_s <= episode.negative_capacity_bpm_s + 1e-6
        assert episode.added_area_bpm_s == pytest.approx(
            episode.removed_area_bpm_s, abs=1e-6
        )


def test_inverse_reconstructs_a_more_concentrated_response_than_observed_hr() -> None:
    observed = _forward_first_order_response()
    result = compute_hrmod_hr_only(
        hr_samples=_samples(observed),
        athlete_profile=_profile(),
        config=_episode_config(),
    )
    clean = [point.clean_hr_bpm for point in result.timeseries]
    hrmod = [point.hrmod_bpm for point in result.timeseries]
    assert max(value for value in hrmod if value is not None) > max(
        value for value in clean if value is not None
    )
    baseline = observed[0]
    clean_above = sum(
        point.dt_s for point in result.timeseries if (point.clean_hr_bpm or 0.0) >= baseline + 20.0
    )
    hrmod_above = sum(
        point.dt_s for point in result.timeseries if (point.hrmod_bpm or 0.0) >= baseline + 20.0
    )
    assert hrmod_above < clean_above


def test_steady_plateau_has_small_correction_away_from_edges() -> None:
    values = [100.0] * 30 + [140.0] * 180 + [100.0] * 100
    result = compute_hrmod_hr_only(
        hr_samples=_samples(values),
        athlete_profile=_profile(),
        config=_episode_config(),
    )
    plateau = [
        point
        for point in result.timeseries
        if 70.0 <= point.elapsed_s <= 180.0
    ]
    assert max(abs(point.raw_correction_bpm or 0.0) for point in plateau) < 0.1


def test_irregular_timestamps_use_actual_dt() -> None:
    times = [0.0, 0.8, 2.3, 3.1, 5.7, 6.2, 8.9]
    result = compute_hrmod_hr_only(
        hr_samples=_samples([110.0] * len(times), times),
        athlete_profile=_profile(),
        config=_episode_config(smoothing_min_points=2, smoothing_window_s=5.0),
    )
    assert [point.dt_s for point in result.timeseries] == pytest.approx(
        [0.0, 0.8, 1.5, 0.8, 2.6, 0.5, 2.7]
    )


def test_long_gap_splits_segments_and_no_episode_crosses_it() -> None:
    first = _forward_first_order_response(duration_s=110, pulse_start_s=25, pulse_end_s=55)
    second = _forward_first_order_response(duration_s=140, pulse_start_s=25, pulse_end_s=55)
    times = [float(index) for index in range(len(first))]
    second_start = times[-1] + 31.0
    times.extend(second_start + index for index in range(len(second)))
    result = compute_hrmod_hr_only(
        hr_samples=_samples(first + second, times),
        athlete_profile=_profile(),
        config=_episode_config(long_gap_threshold_s=10.0),
    )
    assert result.diagnostics.segment_count == 2
    assert result.diagnostics.long_gap_count == 1
    assert {point.segment_id for point in result.timeseries if point.segment_id is not None} == {1, 2}
    assert all(episode.segment_id in {1, 2} for episode in result.episode_summary)


def test_incomplete_end_episode_is_flagged_and_skipped_by_default() -> None:
    values = [100.0] * 30 + [100.0 + 0.8 * index for index in range(1, 61)]
    result = compute_hrmod_hr_only(
        hr_samples=_samples(values),
        athlete_profile=_profile(),
        config=_episode_config(),
    )
    incomplete = [episode for episode in result.episode_summary if not episode.complete]
    assert incomplete
    assert all(not episode.corrected for episode in incomplete)
    assert all(episode.added_area_bpm_s == 0.0 for episode in incomplete)
    assert "INCOMPLETE_EPISODE_END" in result.diagnostics.flags


def test_deadband_suppresses_near_constant_noise() -> None:
    values = [120.0 + 0.08 * math.sin(index / 3.0) for index in range(240)]
    result = compute_hrmod_hr_only(
        hr_samples=_samples(values),
        athlete_profile=_profile(),
        config=_episode_config(correction_deadband_bpm=1.0),
    )
    assert not _corrected_episodes(result)
    assert all(point.hrmod_bpm == pytest.approx(point.clean_hr_bpm) for point in result.timeseries)


def test_zone_classification_uses_unrounded_values() -> None:
    values = [119.999, 120.001, 119.999, 120.001, 119.999, 120.001]
    result = compute_hrmod_hr_only(
        hr_samples=_samples(values),
        athlete_profile=_profile(),
        config=_episode_config(smoothing_min_points=2),
    )
    assert [point.clean_hr_zone for point in result.timeseries] == [
        "Z2",
        "Z3",
        "Z2",
        "Z3",
        "Z2",
        "Z3",
    ]


def test_identical_hr_and_config_are_deterministic() -> None:
    samples = _samples(_forward_first_order_response())
    config = _episode_config()
    first = compute_hrmod_hr_only(
        hr_samples=samples, athlete_profile=_profile(), config=config
    )
    second = compute_hrmod_hr_only(
        hr_samples=samples, athlete_profile=_profile(), config=config
    )
    assert first == second
    assert first.hr_input_hash == second.hr_input_hash


def test_conservative_config_rejects_alpha_above_one() -> None:
    with pytest.raises(ValueError, match="alpha"):
        HRmodConfig(alpha=1.01)
